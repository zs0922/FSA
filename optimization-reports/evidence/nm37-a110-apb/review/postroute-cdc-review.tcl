# Independent read-only routed CDC evidence. This is not a physical PASS gate.
# Run once, after the official post_route.dcp is completely written.
# No read_xdc, set_property, timing exceptions, implementation, or DCP writes.
set review_script [file normalize [info script]]
set review_root [file dirname $review_script]
set review_base [file dirname $review_root]
set review_build [file join $review_base build04]
set review_board [file join $review_build A110 fpga chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config]
set review_dcp [file join $review_board obj post_route.dcp]
set review_auditor [file join $review_root reaudit-staging hbm_audit.tcl]
set review_writer [file join $review_root postroute-cdc-manifest.py]
set review_dir [file join $review_root build04-postroute-cdc-review]
proc review_sha {p} {
 if {![file isfile $p]} {error "Missing input: $p"}
 return [lindex [exec sha256sum -- $p] 0]
}
if {$argc != 1 || ![regexp {^[a-f0-9]{64}$} [lindex $argv 0]]} {
 error "Expected SHA256 of the completed official build04 post_route.dcp"
}
set review_dcp_sha [lindex $argv 0]
if {[review_sha $review_dcp] ne $review_dcp_sha} {error "DCP differs from caller's completed-checkpoint SHA"}
if {[review_sha $review_auditor] ne "fab04cc7b93481f26e8fd799d50e31db08a80b1da576910035c49d8c9d62d139"} {
 error "Read-only query helper differs from reviewed revision"
}
if {[file exists $review_dir]} {error "Refusing to overwrite prior independent review evidence"}
file mkdir $review_dir
set input_paths [list $review_script $review_writer $review_dcp $review_auditor \
 [file join $review_build build.json] \
 [file join $review_base src scripts nm37-a110 physical_audit.py] \
 [file join $review_base src scripts nm37-a110 cdc-baseline.json] \
 [file join $review_board gen-collateral ResetWrangler.sv] \
 [file join $review_board gen-collateral ResetCatchAndSync_d3.sv] \
 [file join $review_board gen-collateral AsyncResetSynchronizerPrimitiveShiftReg_d3_i0.sv] \
 [file join $review_board gen-collateral HBMPLLResetControl.sv] \
 [file join $review_board gen-collateral HBMStartupControl.sv] \
 [file join $review_board obj ip nm37_xdma ip_0 source nm37_xdma_pcie4c_ip_pcie4c_uscale_core_top.v] \
 [file join $review_base src generators rocket-chip src main scala prci ResetWrangler.scala] \
 [file join $review_base src generators rocket-chip src main scala util ResetCatchAndSync.scala]]
set original_hashes [dict create]
set input_fd [open [file join $review_dir input-sha256.tsv] w]
puts $input_fd "sha256\tinput"
foreach p $input_paths {
 set digest [review_sha $p]
 dict set original_hashes $p $digest
 puts $input_fd "$digest\t$p"
}
close $input_fd
set props [open [file join $review_dir properties.txt] w]
puts $props "mode=independent-routed-cdc-evidence"
puts $props "candidate_physical_acceptance=false"
puts $props "checkpoint_sha256=$review_dcp_sha"
puts $props "started_utc=[clock format [clock seconds] -gmt true -format {%Y-%m-%dT%H:%M:%SZ}]"
flush $props

namespace eval routed_cdc {
 variable errors {}
 variable fd
 variable cone_fd
 proc check {name body} {
  variable errors
  variable fd
  set status [catch {uplevel 1 $body} result options]
  if {$status} {
   lappend errors "$name: $result"
   puts $fd "CHECK\t$name\tMISMATCH\t$result"
  } else {
   puts $fd "CHECK\t$name\tMATCH\t$result"
  }
  flush $fd
 }
 proc require {condition message} {
  if {![uplevel 1 [list expr $condition]]} {error $message}
 }
 proc c {name} {return [hbm_audit::one [get_cells -quiet $name] "cell $name"]}
 proc p {name role} {return [hbm_audit::pin [c $name] $role]}
 proc zero_polarity {pin} {
  require {[get_property IS_INVERTED $pin] eq "0"} "Nonzero/unknown pin polarity: $pin"
 }
 proc reg {name type init clk} {
  variable fd
  set cell [c $name]
  require {[get_property REF_NAME $cell] eq $type} "$name type"
  require {[get_property INIT $cell] eq $init} "$name INIT"
  require {[hbm_audit::clock [p $name C]] eq $clk} "$name clock"
  zero_polarity [p $name C]
  foreach pin [lsort [get_pins -quiet -of_objects $cell]] {
   set dir [get_property DIRECTION $pin]
   set driver {}
   if {$dir eq "IN"} {set driver [hbm_audit::drivers $pin]}
   puts $fd "PIN\t$pin\t$dir\tIS_INVERTED=[get_property IS_INVERTED $pin]\tdriver=$driver"
  }
  set ce [hbm_audit::one [hbm_audit::drivers [p $name CE]] "$name CE source"]
  puts $fd "REGISTER\t$name\t$type\t$init\tclock=$clk\tASYNC_REG=[get_property ASYNC_REG $cell]\tCE_source=$ce"
  return $cell
 }
 # Record only real combinational cone pins, not every pin of a large hard IP.
 proc cone {target} {
  variable cone_fd
  set pin [hbm_audit::one [get_pins -quiet $target] "cone target"]
  puts $cone_fd "\nTARGET $target"
  puts $cone_fd "driver=[hbm_audit::drivers $pin]"
  puts $cone_fd "fanin_startpoints=[hbm_audit::fanin $pin]"
  set pins [lsort -unique [concat [all_fanin -flat -to $pin] [list $pin]]]
  foreach cell [lsort -unique [get_cells -quiet -of_objects $pins]] {
   puts $cone_fd "CELL $cell REF=[get_property REF_NAME $cell] INIT=[get_property INIT $cell] ASYNC_REG=[get_property ASYNC_REG $cell]"
   foreach q [get_pins -quiet -of_objects $cell] {
    if {[lsearch -exact $pins $q] < 0} {continue}
    puts $cone_fd " PIN $q INV=[get_property IS_INVERTED $q] driver=[hbm_audit::drivers $q]"
   }
  }
  flush $cone_fd
 }
 # An exhaustive Boolean check of an actual small reset cone. Only explicit
 # leaves, LUTs, INV and strict transparent buffers are accepted; extra FFs,
 # clock primitives, unknown polarities and cycles are rejected.
 proc value {target leaves values {seen {}}} {
  if {[dict exists $leaves $target]} {return [dict get $values [dict get $leaves $target]]}
  require {[llength $seen] < 16 && [lsearch -exact $seen $target] < 0} "Reset cone cycle/depth at $target"
  set d [hbm_audit::one [hbm_audit::drivers $target] "reset cone driver"]
  if {[dict exists $leaves $d]} {return [dict get $values [dict get $leaves $d]]}
  set cell [hbm_audit::one [get_cells -quiet -of_objects $d] "reset cone cell"]
  set type [get_property REF_NAME $cell]
  set next [concat $seen [list $target]]
  if {$type eq "VCC"} {return 1}
  if {$type eq "GND"} {return 0}
  if {$type eq "INV"} {return [expr {1 - [value [hbm_audit::pin $cell I] $leaves $values $next]}]}
  if {$type in {BUFG BUFGCE}} {
   variable fd
   hbm_audit::checked_buffer $cell $fd boolean_buffer
   return [value [hbm_audit::pin $cell I] $leaves $values $next]
  }
  require {[regexp {^LUT([1-6])$} $type -> n]} "Unexpected reset cone cell $cell ($type)"
  set index 0
  for {set i 0} {$i < $n} {incr i} {
   set ip [hbm_audit::pin $cell I$i]
   zero_polarity $ip
   set bit [value $ip $leaves $values $next]
   set index [expr {$index | ($bit << $i)}]
  }
  zero_polarity [hbm_audit::pin $cell O]
  set init [get_property INIT $cell]
  require {[regexp {^[0-9]+'h([0-9A-Fa-f]+)$} $init -> hex]} "Unknown LUT INIT $cell:$init"
  scan $hex %x bits
  return [expr {($bits >> $index) & 1}]
 }
 proc truth {target leaves variables expression} {
  variable fd
  set cases [expr {1 << [llength $variables]}]
  for {set mask 0} {$mask < $cases} {incr mask} {
   set values [dict create]
   for {set i 0} {$i < [llength $variables]} {incr i} {
    set name [lindex $variables $i]
    set $name [expr {($mask >> $i) & 1}]
    dict set values $name [set $name]
   }
   set wanted [expr $expression]
   set got [value $target $leaves $values]
   require {$got == $wanted} "Wrong reset truth $target at $values: got$got expected$wanted"
   puts $fd "TRUTH\t$target\t$values\t$got"
  }
  return "$cases actual netlist truth rows match"
 }
 proc direct {target expected} {
  zero_polarity $target
  zero_polarity $expected
  require {[hbm_audit::drivers $target] eq [list $expected]} "Unexpected direct connection $target"
 }
 proc vcc {target} {
  set d [hbm_audit::one [hbm_audit::drivers $target] "constant-one driver"]
  require {[get_property REF_NAME [get_cells -of_objects $d]] eq "VCC"} "Not constant one: $target"
  zero_polarity $target
 }
}

set opened false
set run_error [catch {
 set_param general.maxThreads 2
 open_checkpoint $review_dcp
 set opened true
 update_timing
 # Pure query helpers only. Do not invoke hbm_audit::run or alter constraints.
 source $review_auditor
 puts $props "part=[get_property PART [current_design]]"
 report_cdc -details -file [file join $review_dir cdc.rpt]
 report_cdc -details -all_checks_per_endpoint -no_waiver -file [file join $review_dir cdc-all-checks.rpt]
 report_clock_interaction -file [file join $review_dir clock-interaction.rpt]
 report_exceptions -file [file join $review_dir exceptions.rpt]
 check_timing -verbose -file [file join $review_dir check-timing.rpt]
 set routed_cdc::fd [open [file join $review_dir structure.tsv] w]
 set routed_cdc::cone_fd [open [file join $review_dir reset-cones.txt] w]
 set pci pcie/blackbox/inst/pcie4c_ip_i/inst
 set core [hbm_audit::one [get_cells -quiet -hier -filter {REF_NAME == PCIE4CE4}] "PCIe hard primitive"]
 set qualified [routed_cdc::p {pcieResetControl/releaseSync/sync_reg[2]} Q]
 set released [routed_cdc::p pcieResetControl/released_reg Q]
 set por [routed_cdc::p powerOnReset_fpga_power_on/power_on_reset_reg Q]
 set hot [hbm_audit::pin $core CFGHOTRESETOUT]
 set leaves [dict create $qualified qualified $released released $por por $hot hot]
 foreach {suffix type init clk role} {
  as_mac_in_detect_user_reg FDPE 1'b1 _pcie_io_clocks_axi_aclk PRE
  {pipe_tx_rate_ff_reg[0]} FDCE 1'b0 pipe_clk CLR
  {pipe_tx_rate_ff_reg[1]} FDCE 1'b0 pipe_clk CLR
  speed_change_in_progress_reg FDCE 1'b0 pipe_clk CLR
 } {
  set name $pci/$suffix
  routed_cdc::check $suffix {
   routed_cdc::reg $name $type $init $clk
   set target [routed_cdc::p $name $role]
   routed_cdc::zero_polarity $target
   routed_cdc::cone $target
   routed_cdc::truth $target $leaves {qualified released por hot} {$hot || $por || !$qualified || !$released}
  }
 }
 # Installed vendor XPM reset synchronizer and user-reset output, not an
 # assumption that the four preceding internal registers have synchronizers.
 set user_reset_leaves $leaves
 dict set user_reset_leaves [hbm_audit::pin $core CFGPHYLINKDOWN] linkdown
 dict set user_reset_leaves [hbm_audit::pin $core {CFGPHYLINKSTATUS[1]}] linkup
 set xpm0 [format {%s/user_reset_cdc/arststages_ff_reg[0]} $pci]
 set xpm1 [format {%s/user_reset_cdc/arststages_ff_reg[1]} $pci]
 foreach cell [list $xpm0 $xpm1] {
  routed_cdc::check $cell {
   set ff [routed_cdc::reg $cell FDPE 1'b0 _pcie_io_clocks_axi_aclk]
   routed_cdc::require {[hbm_audit::truth [get_property ASYNC_REG $ff]]} "Missing vendor XPM ASYNC_REG"
   routed_cdc::vcc [routed_cdc::p $cell CE]
   routed_cdc::zero_polarity [routed_cdc::p $cell PRE]
   routed_cdc::cone [routed_cdc::p $cell PRE]
   routed_cdc::truth [routed_cdc::p $cell PRE] $user_reset_leaves {qualified released por hot linkdown linkup} {$hot || $por || !$qualified || !$released || $linkdown || !$linkup}
  }
 }
 routed_cdc::check user_reset_release_sequence {
  set first [hbm_audit::one [hbm_audit::drivers [routed_cdc::p $xpm0 D]] "XPM first D"]
  routed_cdc::zero_polarity [routed_cdc::p $xpm0 D]
  routed_cdc::require {[get_property REF_NAME [get_cells -of_objects $first]] eq "GND"} "XPM first D not zero"
  routed_cdc::direct [routed_cdc::p $xpm1 D] [routed_cdc::p $xpm0 Q]
  routed_cdc::reg $pci/user_reset_reg FDPE 1'b1 _pcie_io_clocks_axi_aclk
  routed_cdc::direct [routed_cdc::p $pci/user_reset_reg D] [routed_cdc::p $xpm1 Q]
  routed_cdc::truth [routed_cdc::p $pci/user_reset_reg PRE] $user_reset_leaves {qualified released por hot linkdown linkup} {$hot || $por || !$qualified || !$released || $linkdown || !$linkup}
 }
 set apb clk_out2_harnessSysPLL
 routed_cdc::check actual_apb_clock {hbm_audit::period [get_clocks $apb] [expr {1000.0/55.0}]}
 set wrangler_leaves [dict create $por por [hbm_audit::pin [hbm_audit::one [get_cells -hier -filter {REF_NAME == MMCME4_ADV && NAME =~ harnessSysPLL/*}] "DUT PLL"] LOCKED] locked [routed_cdc::p $pci/user_reset_reg Q] user_reset]
 for {set i 0} {$i < 13} {incr i} {
  set name [format {wrangler/debounced_debounce/reg_0_reg[%d]} $i]
  routed_cdc::check debounce_bit_$i {
   routed_cdc::reg $name FDCE 1'b0 $apb
   set target [routed_cdc::p $name CLR]
   routed_cdc::zero_polarity $target
   routed_cdc::cone $target
   routed_cdc::truth $target $wrangler_leaves {por locked user_reset} {$por || !$locked || $user_reset}
  }
 }
 set deglitch wrangler/deglitched_deglitch/reg_0_reg
 routed_cdc::check deglitch {
  routed_cdc::reg $deglitch FDPE 1'b1 $apb
  routed_cdc::zero_polarity [routed_cdc::p $deglitch PRE]
  routed_cdc::cone [routed_cdc::p $deglitch PRE]
  routed_cdc::truth [routed_cdc::p $deglitch PRE] $wrangler_leaves {por locked user_reset} {$por || !$locked || $user_reset}
 }
 set catcher wrangler/nodeOut_reset_catcher_2/io_sync_reset_chain/output_chain
 foreach i {2 1 0} {
  set name $catcher/sync_${i}_reg
  routed_cdc::check pcie_wrangler_release_$i {
   routed_cdc::reg $name FDCE 1'b0 _pcie_io_clocks_axi_aclk
   routed_cdc::vcc [routed_cdc::p $name CE]
   routed_cdc::zero_polarity [routed_cdc::p $name CLR]
   routed_cdc::direct [routed_cdc::p $name CLR] [routed_cdc::p $deglitch Q]
   if {$i == 2} {
    routed_cdc::vcc [routed_cdc::p $name D]
   } else {
    routed_cdc::direct [routed_cdc::p $name D] [routed_cdc::p $catcher/sync_[expr {$i+1}]_reg Q]
   }
  }
 }
 puts $routed_cdc::fd "structural_mismatches=[llength $routed_cdc::errors]"
 close $routed_cdc::fd
 close $routed_cdc::cone_fd
 puts $props "structural_mismatches=[llength $routed_cdc::errors]"
 foreach p $input_paths {
  if {[review_sha $p] ne [dict get $original_hashes $p]} {error "Input changed during independent evidence generation: $p"}
 }
} run_message run_options]
if {$run_error} {
 puts $props "report_generation=failed"
 puts $props "error=$run_message"
 set error_fd [open [file join $review_dir failure.txt] w]
 puts $error_fd $run_message
 puts $error_fd [dict get $run_options -errorinfo]
 close $error_fd
} else {
 puts $props "report_generation=completed"
}
puts $props "ended_utc=[clock format [clock seconds] -gmt true -format {%Y-%m-%dT%H:%M:%SZ}]"
close $props
if {$opened} {close_design}
exec python3 $review_writer $review_dir $review_dcp $review_build $review_script
puts "Independent routed CDC evidence: $review_dir"
if {$run_error} {return -options $run_options $run_message}
exit
