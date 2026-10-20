# Read-only routed HBM clock/startup audit. Errors leave all preceding evidence.
# No timing exceptions or implementation properties are changed here.
namespace eval hbm_audit {
 proc one {objects description} {
  if {[llength $objects] != 1} {error "HBM audit: expected one $description, found $objects"}
  return [lindex $objects 0]
 }
 proc pin {cell role} {
  return [one [get_pins -quiet -of_objects $cell -filter "REF_PIN_NAME == $role"] "$cell/$role"]
 }
 proc drivers {p} {
  set nets [get_nets -quiet -segments -of_objects $p]
  return [lsort -unique [concat [get_pins -quiet -leaf -of_objects $nets -filter {DIRECTION == OUT}] [get_ports -quiet -of_objects $nets -filter {DIRECTION == IN}]]]
 }
 proc loads {p} {
  return [lsort -unique [get_pins -quiet -leaf -of_objects [get_nets -quiet -segments -of_objects $p] -filter {DIRECTION == IN}]]
 }
 proc transparent_connection {p expected fd label} {
  set trace {}
  for {set i 0} {$i < 8} {incr i} {
   set driver [one [drivers $p] "$label driver"]
   lappend trace $driver
   if {$driver eq $expected} {puts $fd "$label=$trace"; return}
   set c [one [get_cells -quiet -of_objects $driver] "$label buffer"]
   set primitive [get_property REF_NAME $c]
   if {$primitive ni {BUFG BUFGCE}} {error "HBM audit: $label has nontransparent source $driver ($primitive)"}
   foreach property {IS_I_INVERTED IS_CE_INVERTED} {
    if {[lsearch -exact [list_property $c] $property] >= 0 && [string toupper [get_property $property $c]] ni {0 FALSE NO 1'B0 1'H0}} {error "HBM audit: inverted/unknown buffer polarity in $label"}
   }
   if {$primitive eq "BUFGCE"} {
    set enable [one [drivers [pin $c CE]] "$label buffer enable"]
    if {[get_property REF_NAME [get_cells -quiet -of_objects $enable]] ne "VCC"} {error "HBM audit: gated buffer in $label"}
   }
   set p [pin $c I]
  }
  error "HBM audit: excessive or cyclic buffer chain in $label"
 }
 proc clock {p} {return [one [get_clocks -quiet -of_objects $p] "clock at $p"]}
 proc root_clock {c} {
  set seen {}
  while {[get_property IS_GENERATED $c]} {
   if {[lsearch -exact $seen $c] >= 0} {error "HBM audit: cyclic generated clocks"}
   lappend seen $c
   set c [one [get_clocks -quiet [get_property MASTER_CLOCK $c]] "master clock of $c"]
  }
  return $c
 }
 proc truth {v} {return [expr {[string toupper $v] in {1 TRUE YES}}]}
 proc period {c wanted} {
  set got [get_property PERIOD $c]
  if {![string is double -strict $got] || abs($got-$wanted)>0.001} {error "HBM audit: $c period $got != $wanted ns"}
 }
 proc chain {name expected_clock fd} {
  # Keep exact bit order: first stage is sync[0], output stage sync[2].
  set cells {}
  for {set i 0} {$i < 3} {incr i} {
   set matches {}
   foreach candidate [get_cells -quiet -hier -filter "NAME =~ */$name/* && REF_NAME == FDCE"] {
    if {[regexp [format {/%s/sync_reg\[%d\]$} $name $i] $candidate]} {lappend matches $candidate}
   }
   set c [one $matches "$name stage $i FDCE"]
   if {![truth [get_property ASYNC_REG $c]]} {error "HBM audit: ASYNC_REG missing on $c"}
   if {[get_property INIT $c] ni {0 1'b0}} {error "HBM audit: expected zero INIT at $c"}
   if {[clock [pin $c C]] ne $expected_clock} {error "HBM audit: wrong clock at $c"}
   puts $fd "$name\t$i\t$c\t[get_property REF_NAME $c]\t[get_property LOC $c]\t[get_property ASYNC_REG $c]\t[drivers [pin $c D]]\t[drivers [pin $c CLR]]\t[loads [pin $c Q]]"
   lappend cells $c
  }
  set reset_driver [drivers [pin [lindex $cells 0] CLR]]
  one $reset_driver "$name reset driver"
  foreach c $cells {
   if {[drivers [pin $c CLR]] ne $reset_driver} {error "HBM audit: non-common reset in $name"}
   set enable [one [drivers [pin $c CE]] "$name enable driver"]
   if {[get_property REF_NAME [get_cells -quiet -of_objects $enable]] ne "VCC"} {error "HBM audit: $name synchronizer has a clock enable"}
  }
  foreach i {0 1} {
   set a [lindex $cells $i]; set b [lindex $cells [expr {$i+1}]]
   if {[drivers [pin $b D]] ne [list [pin $a Q]]} {error "HBM audit: $name is not a direct three-stage chain"}
   if {[loads [pin $a Q]] ne [list [pin $b D]]} {error "HBM audit: intermediate $name stage has extra fanout"}
  }
  return $cells
 }
 proc timed {from to label dir fd} {
  set args [list -to $to]
  if {[llength $from]} {lappend args -from $from}
  foreach kind {max min} {
   set paths [get_timing_paths -quiet {*}$args -delay_type $kind -max_paths 1]
   set path [one $paths "$label $kind timing path (must not be cut)"]
   set slack [get_property SLACK $path]
   puts $fd "$label\t$kind\t$slack\t[get_property STARTPOINT_PIN $path]\t[get_property ENDPOINT_PIN $path]"
   report_timing {*}$args -delay_type $kind -max_paths 10 -input_pins -nets -file $dir/${label}_${kind}.rpt
   if {![string is double -strict $slack] || $slack < 0} {error "HBM audit: $label $kind slack $slack"}
  }
 }
 proc fanin {p} {
  # Vivado 2020.2 returns a launching register's C pin as a timing
  # startpoint. Normalize only the known single-output FF primitives to Q
  # so structural sources can be compared with directly connected drivers.
  set sources {}
  foreach point [all_fanin -flat -startpoints_only -to $p] {
   if {[get_property CLASS $point] eq "pin" && [get_property REF_PIN_NAME $point] eq "C"} {
    set c [one [get_cells -quiet -of_objects $point] "fanin source cell"]
    if {[get_property REF_NAME $c] in {FDCE FDRE FDPE FDSE}} {set point [pin $c Q]}
   }
   lappend sources $point
  }
  return [lsort -unique $sources]
 }
 proc monitor {ref dir pf sf tf} {
  set m [one [get_cells -quiet pcieResetControl] "PCIe cold-reset controller"]
  set pll [one [get_cells -quiet harnessSysPLL] "harness PLL"]
  set pll_locked [drivers [pin $pll locked]]
  one $pll_locked "PLL lock source"
  set por_pin [one [get_pins -quiet powerOnReset_fpga_power_on/power_on_reset] "power-on reset output"]
  set por [drivers $por_pin]
  one $por "power-on reset source"
  set perst [one [get_ports -quiet prst_n] "external PERST"]
  if {[drivers [pin $pll reset]] ne $por} {error "HBM audit: PCIe reset must never feed back into PLL reset"}
  set cells [chain releaseSync $ref $sf]
  if {[file dirname [file dirname [lindex $cells 0]]] ne $m} {error "HBM audit: releaseSync is outside PCIe reset controller"}
  set constant [one [drivers [pin [lindex $cells 0] D]] "PCIe reset-release constant"]
  if {[get_property REF_NAME [get_cells -quiet -of_objects $constant]] ne "VCC"} {error "HBM audit: PCIe reset synchronizer is not fed constant one"}
  set expected_causes [lsort -unique [concat $pll_locked $por [list $perst]]]
  foreach c $cells {
   if {[fanin [pin $c CLR]] ne $expected_causes} {error "HBM audit: PCIe reset synchronizer has unexpected reset feedback"}
  }
  set qualified [pin [lindex $cells 2] Q]
  set released [one [get_cells -quiet $m/released_reg] "PCIe released register"]
  set count {}
  for {set i 0} {$i < 14} {incr i} {
   set matches {}
   foreach c [get_cells -quiet -hier -filter "NAME =~ $m/stableCount_reg* && REF_NAME == FDCE"] {
    if {[regexp [format {/stableCount_reg\[%d\]$} $i] $c]} {lappend matches $c}
   }
   lappend count [one $matches "PCIe stable counter bit $i"]
  }
  foreach c [concat $count [list $released]] {
   if {[get_property REF_NAME $c] ne "FDCE" || [get_property INIT $c] ni {0 1'b0}} {error "HBM audit: unexpected cold-reset counter primitive/init at $c"}
   if {[clock [pin $c C]] ne $ref} {error "HBM audit: cold-reset counter is not in board reference domain"}
   if {[fanin [pin $c CLR]] ne [list $qualified]} {error "HBM audit: cold-reset counter must reset from qualified reset release"}
  }
  set expected_output [lsort [list $qualified [pin $released Q]]]
  # Audit the surviving FF and XDMA endpoints. Aliased module IO pins can
  # legitimately have no net after optimization and add no independent proof.
  set pcie [one [get_cells -quiet pcie/blackbox] "XDMA IP"]
  set pcie_reset [pin $pcie sys_rst_n]
  if {[fanin $pcie_reset] ne [lsort -unique [concat $expected_output $por]]} {error "HBM audit: XDMA reset is not driven by qualified cold reset and POR"}
  timed [pin [lindex $cells 0] Q] [pin [lindex $cells 1] D] pcie_reset_stage01 $dir $tf
  timed [pin [lindex $cells 1] Q] [pin [lindex $cells 2] D] pcie_reset_stage12 $dir $tf
  timed $qualified [pin $released CLR] pcie_released_clr $dir $tf
  for {set i 0} {$i < 14} {incr i} {
   timed $qualified [pin [lindex $count $i] CLR] pcie_counter_clr$i $dir $tf
  }
  # Raw PLL lock/PERST/POR are intentionally asynchronous reset causes. Record
  # their coverage without pretending absent source timing is a synchronous
  # recovery/removal pass. The three ASYNC_REG stages and all qualified release
  # paths above are checked separately; no new exception is introduced.
  set clrs [get_pins -quiet -of_objects $cells -filter {REF_PIN_NAME == CLR}]
  foreach kind {max min} {
   report_timing -to $clrs -delay_type $kind -max_paths 10 -input_pins -nets -file $dir/pcie_raw_reset_${kind}.rpt
  }
  puts $pf "pcie_reset_monitor_clock=$ref"
  puts $pf "pcie_reset_synchronizer_stages=3"
  puts $pf "pcie_reset_counter_bits=14"
  puts $pf "pcie_raw_reset_sources=$expected_causes"
  puts $pf "pcie_reset_pin=$pcie_reset"
  puts $pf "pcie_reset_feedback_free=1"
 }
 proc run {dir} {
  file mkdir $dir
  set pf [open $dir/properties.txt w]
  set sf [open $dir/synchronizers.tsv w]
  set tf [open $dir/endpoint_timing.tsv w]
  set status [catch {
   puts $sf "chain\tstage\tcell\tprimitive\tlocation\tASYNC_REG\tD_driver\tCLR_driver\tQ_loads"
   puts $tf "path\tcheck\tslack_ns\tstart\tend"
   # Check actual hard-block pins. Unused/aliased hierarchical wrapper pins
   # can remain present without a connected net after optimization.
   set hbm [one [get_cells -quiet dutDomain/ram_hbm] "HBM IP"]
   set apb_primitive [one [get_cells -quiet -hier -filter {REF_NAME == HBM_SNGLBLI_INTF_APB}] "HBM APB primitive"]
   set axi_matches {}
   foreach candidate [get_cells -quiet -hier -filter {REF_NAME == HBM_SNGLBLI_INTF_AXI}] {
    if {[string match {*HBM_SNGLBLI_INTF_AXI<0>_INST} $candidate]} {lappend axi_matches $candidate}
   }
   set axi_primitive [one $axi_matches "active HBM AXI channel zero primitive"]
   set ref_primitive [one [get_cells -quiet -hier -filter {REF_NAME == HBM_REF_CLK}] "HBM reference primitive"]
   set apb_pin [pin $apb_primitive PCLK]
   set apb [clock $apb_pin]
   set axi_pin [pin $axi_primitive ACLK]
   set ref_pin [pin $ref_primitive REF_CLK]
   set axi [clock $axi_pin]
   set ref_hard_clock [clock $ref_pin]
   # The vendor HBM XDC intentionally creates its own primary reference
   # clock inside the IP. Verify both actual hard-block period and the
   # board source through the IP's single reference BUFG.
   set ref [clock [pin $hbm HBM_REF_CLK_0]]
   set ref_driver [one [drivers $ref_pin] "HBM reference buffer output"]
   set ref_buffer [one [get_cells -quiet -of_objects $ref_driver] "HBM reference buffer"]
   if {[get_property REF_NAME $ref_buffer] ne "BUFG"} {error "HBM audit: unexpected HBM reference buffer primitive"}
   transparent_connection $ref_pin [one [drivers [pin $hbm HBM_REF_CLK_0]] "board reference source"] $pf ref_buffer_chain
   period $axi [expr {1000.0/110.0}]
   period $apb [expr {1000.0/55.0}]
   period $ref 10.0
   period $ref_hard_clock 10.0
   if {$axi eq $apb || $apb eq $ref} {error "HBM audit: expected separate generated APB, DUT and reference clocks"}
   if {$axi ne [get_clocks clk_out1_harnessSysPLL]} {error "HBM audit: AXI not on DUT clock"}
   if {$apb ne [get_clocks clk_out2_harnessSysPLL]} {error "HBM audit: APB not on second harness PLL output"}
   if {abs([get_property PERIOD $apb] - 2.0*[get_property PERIOD $axi]) > 0.001} {error "HBM audit: APB/DUT ratio must be 1:2"}
   if {[root_clock $axi] ne [root_clock $apb] || [root_clock $axi] ne $ref} {error "HBM audit: DUT/APB must share the board reference clock"}
   set wave [get_property WAVEFORM $apb]
   set high [expr {[lindex $wave 1]-[lindex $wave 0]}]
   set low [expr {[get_property PERIOD $apb]-$high}]
   if {$high < 5.0 || $low < 5.0} {error "HBM audit: APB waveform violates 5 ns minimum pulse"}
   foreach {key value} [list apb_pin $apb_pin axi_pin $axi_pin ref_pin $ref_pin apb_clock $apb apb_period [get_property PERIOD $apb] apb_high $high apb_low $low axi_clock $axi axi_period [get_property PERIOD $axi] ref_clock $ref ref_period [get_property PERIOD $ref] ref_hard_clock $ref_hard_clock ref_hard_period [get_property PERIOD $ref_hard_clock] primary_clock [root_clock $axi]] {puts $pf "$key=$value"}
   # Chisel prefixes the explicit suggestion with the enclosing Scala val.
   # These are the exact instance names checked in emitted production RTL.
   set a [chain apbResetN_apbResetSync $apb $sf]
   set x [chain axiResetN_axiResetSync $axi $sf]
   set c [chain completeAxi_completeSync $axi $sf]
   foreach cells [list $a $x] {
    set constant [one [drivers [pin [lindex $cells 0] D]] "reset-release constant"]
    if {[get_property REF_NAME [get_cells -quiet -of_objects $constant]] ne "VCC"} {error "HBM audit: reset synchronizer is not fed constant one"}
   }
   set startup [file dirname [file dirname [lindex $a 0]]]
   foreach cells [list $x $c] {
    if {[file dirname [file dirname [lindex $cells 0]]] ne $startup} {error "HBM audit: synchronizers are not in one startup controller"}
   }
   puts $pf "startup_cell=$startup"
   set common_reset [one [drivers [pin $startup io_reset]] "HBM common cold-reset source"]
   set common_causes [fanin [pin $startup io_reset]]
   if {![llength $common_causes]} {error "HBM audit: common cold-reset source has no startpoint"}
   if {[drivers [pin [lindex $a 0] CLR]] ne [list $common_reset]} {error "HBM audit: APB reset does not directly follow common cold reset"}
   if {[fanin [pin [lindex $x 0] CLR]] ne [lsort -unique [concat $common_causes [list [pin [lindex $a 2] Q]]]]} {error "HBM audit: AXI reset must depend only on common reset and APB reset release"}
   if {[fanin [pin [lindex $c 0] CLR]] ne [list [pin [lindex $x 2] Q]]} {error "HBM audit: completion synchronization reset must follow AXI reset release"}
   set complete [one [get_cells -quiet $startup/completeApb_reg] "APB completion source register"]
   if {[get_property REF_NAME $complete] ne "FDCE"} {error "HBM audit: unexpected completion source primitive"}
   if {[clock [pin $complete C]] ne $apb} {error "HBM audit: completion source not in APB domain"}
   set completion_source [one [drivers [pin $hbm apb_complete_0]] "HBM initialization-complete source"]
   if {[drivers [pin $complete D]] ne [list $completion_source]} {error "HBM audit: completion source not driven by HBM completion"}
   if {[fanin [pin $complete CLR]] ne [list [pin [lindex $a 2] Q]]} {error "HBM audit: APB completion capture reset must follow APB reset release"}
   if {[drivers [pin [lindex $c 0] D]] ne [list [pin $complete Q]]} {error "HBM audit: completion input is not directly source-registered"}
   if {[loads [pin $complete Q]] ne [list [pin [lindex $c 0] D]]} {error "HBM audit: completion crosses by more than one path"}
   transparent_connection [pin $apb_primitive PRESET_N] [pin [lindex $a 2] Q] $pf apb_reset_buffer_chain
   transparent_connection [pin $axi_primitive ARESET_N] [pin [lindex $x 2] Q] $pf axi_reset_buffer_chain
   foreach cells [list $a $x] {
    # Init state must not hold its own hardware producer in reset.
    foreach point [all_fanin -flat -startpoints_only -to [pin [lindex $cells 0] CLR]] {
     foreach forbidden {complete started fault} {
      if {[string match "$startup/$forbidden*" $point]} {error "HBM audit: initialization/reset feedback at $point"}
     }
    }
   }
   # A real cross-clock path must remain timed; a blanket asynchronous group
   # or false path would otherwise make a structurally sound CDC look faster.
   timed [pin $complete Q] [pin [lindex $c 0] D] complete_apb_to_axi $dir $tf
   foreach cells [list $a $x $c] name {apb_reset axi_reset completion_sync} {
    timed [pin [lindex $cells 0] Q] [pin [lindex $cells 1] D] ${name}_stage01 $dir $tf
    timed [pin [lindex $cells 1] Q] [pin [lindex $cells 2] D] ${name}_stage12 $dir $tf
    foreach stage {0 1 2} {
     timed {} [pin [lindex $cells $stage] CLR] ${name}_clr${stage} $dir $tf
    }
   }
   foreach name {started fault} {
    set ff [one [get_cells -quiet $startup/${name}_reg] "startup $name register"]
    if {[get_property REF_NAME $ff] ne "FDCE"} {error "HBM audit: unexpected $name primitive"}
    if {[clock [pin $ff C]] ne $axi} {error "HBM audit: $name outside DUT clock"}
   }
   monitor $ref $dir $pf $sf $tf
   # Vivado 2020.2 has no -from/-to filter on report_clock_interaction.
   # Save its complete matrix and explicitly identify the pair to review.
   report_clock_interaction -file $dir/clock_interaction.rpt
   puts $pf "related_clock_review_pair=$apb $axi"
   puts $pf "synchronizer_stages=9"
   puts $pf "completion_crossing_timed=1"
   puts $pf "status=PASS"
  } message options]
  if {$status} {puts $pf "status=FAIL"; puts $pf "error=$message"}
  close $pf; close $sf; close $tf
  if {$status} {return -options $options $message}
 }
}
