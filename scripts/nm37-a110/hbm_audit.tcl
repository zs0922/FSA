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
 proc run {dir} {
  file mkdir $dir
  set pf [open $dir/properties.txt w]
  set sf [open $dir/synchronizers.tsv w]
  set tf [open $dir/endpoint_timing.tsv w]
  set status [catch {
   puts $sf "chain\tstage\tcell\tprimitive\tlocation\tASYNC_REG\tD_driver\tCLR_driver\tQ_loads"
   puts $tf "path\tcheck\tslack_ns\tstart\tend"
   # Check actual hard-block APB clock, plus preserved IP boundary pins for
   # AXI and the PHY reference. They must have exactly one propagated clock.
   set hbm [one [get_cells -quiet dutDomain/ram_hbm] "HBM IP"]
   set apb_primitive [one [get_cells -quiet -hier -filter {REF_NAME == HBM_SNGLBLI_INTF_APB}] "HBM APB primitive"]
   set apb_pin [pin $apb_primitive PCLK]
   set apb [clock $apb_pin]
   set axi [clock [pin $hbm AXI_00_ACLK]]
   set ref [clock [pin $hbm HBM_REF_CLK_0]]
   period $axi [expr {1000.0/110.0}]
   period $apb [expr {1000.0/55.0}]
   period $ref 10.0
   if {$axi eq $apb || $apb eq $ref} {error "HBM audit: expected separate generated APB, DUT and reference clocks"}
   if {$axi ne [get_clocks clk_out1_harnessSysPLL]} {error "HBM audit: AXI not on DUT clock"}
   if {$apb ne [get_clocks clk_out2_harnessSysPLL]} {error "HBM audit: APB not on second harness PLL output"}
   if {abs([get_property PERIOD $apb] - 2.0*[get_property PERIOD $axi]) > 0.001} {error "HBM audit: APB/DUT ratio must be 1:2"}
   if {[clock [pin $hbm APB_0_PCLK]] ne $apb} {error "HBM audit: APB wrapper/primitive clocks disagree"}
   if {[root_clock $axi] ne [root_clock $apb] || [root_clock $axi] ne $ref} {error "HBM audit: DUT/APB must share the board reference clock"}
   set wave [get_property WAVEFORM $apb]
   set high [expr {[lindex $wave 1]-[lindex $wave 0]}]
   set low [expr {[get_property PERIOD $apb]-$high}]
   if {$high < 5.0 || $low < 5.0} {error "HBM audit: APB waveform violates 5 ns minimum pulse"}
   foreach {key value} [list apb_pin $apb_pin apb_clock $apb apb_period [get_property PERIOD $apb] apb_high $high apb_low $low axi_clock $axi axi_period [get_property PERIOD $axi] ref_clock $ref ref_period [get_property PERIOD $ref] primary_clock [root_clock $axi]] {puts $pf "$key=$value"}
   set a [chain apbResetSync $apb $sf]
   set x [chain axiResetSync $axi $sf]
   set c [chain completeSync $axi $sf]
   foreach cells [list $a $x] {
    set constant [one [drivers [pin [lindex $cells 0] D]] "reset-release constant"]
    if {[get_property REF_NAME [get_cells -quiet -of_objects $constant]] ne "VCC"} {error "HBM audit: reset synchronizer is not fed constant one"}
   }
   set startup [file dirname [file dirname [lindex $a 0]]]
   foreach cells [list $x $c] {
    if {[file dirname [file dirname [lindex $cells 0]]] ne $startup} {error "HBM audit: synchronizers are not in one startup controller"}
   }
   puts $pf "startup_cell=$startup"
   set complete [one [get_cells -quiet $startup/completeApb_reg] "APB completion source register"]
   if {[get_property REF_NAME $complete] ne "FDCE"} {error "HBM audit: unexpected completion source primitive"}
   if {[clock [pin $complete C]] ne $apb} {error "HBM audit: completion source not in APB domain"}
   if {[drivers [pin $complete D]] ne [drivers [pin $hbm apb_complete_0]]} {error "HBM audit: completion source not driven by HBM completion"}
   if {[drivers [pin [lindex $c 0] D]] ne [list [pin $complete Q]]} {error "HBM audit: completion input is not directly source-registered"}
   if {[loads [pin $complete Q]] ne [list [pin [lindex $c 0] D]]} {error "HBM audit: completion crosses by more than one path"}
   if {[drivers [pin $hbm APB_0_PRESET_N]] ne [list [pin [lindex $a 2] Q]]} {error "HBM audit: APB reset not from APB synchronizer output"}
   if {[drivers [pin $hbm AXI_00_ARESET_N]] ne [list [pin [lindex $x 2] Q]]} {error "HBM audit: AXI reset not from AXI synchronizer output"}
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
   report_clock_interaction -from $apb -to $axi -file $dir/apb_to_axi_clocks.rpt
   report_clock_interaction -from $axi -to $apb -file $dir/axi_to_apb_clocks.rpt
   puts $pf "synchronizer_stages=9"
   puts $pf "completion_crossing_timed=1"
   puts $pf "status=PASS"
  } message options]
  if {$status} {puts $pf "status=FAIL"; puts $pf "error=$message"}
  close $pf; close $sf; close $tf
  if {$status} {return -options $options $message}
 }
}
