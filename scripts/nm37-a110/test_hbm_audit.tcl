# Offline API doubles for failure modes of the corrected helpers. This tests
# acceptance logic only; actual Vivado structures are checked separately.
set root [file dirname [file normalize [info script]]]
set auditor [file join $root hbm_audit.tcl]
source $auditor
puts "mode=offline-helper-negative-tests"
puts "candidate_physical_acceptance=false"
puts "auditor_sha256=[lindex [exec sha256sum -- $auditor] 0]"
puts "test_sha256=[lindex [exec sha256sum -- [info script]] 0]"
set trace [open /dev/null w]
proc get_property {property object} {
 if {[dict exists $::props $object $property]} {return [dict get $::props $object $property]}
 if {$property eq "IS_INVERTED"} {return 0}
 return {}
}
proc list_property {object} {
 if {[dict exists $::missing $object]} {return {}}
 return {IS_INVERTED}
}
proc get_cells {args} {
 set object [lindex $args end]
 if {[lsearch -exact $args -of_objects] >= 0} {return [file dirname $object]}
 if {[dict exists $::props $object REF_NAME]} {return $object}
 return {}
}
proc hbm_audit::pin {cell role} {return "$cell/$role"}
proc hbm_audit::drivers {pin} {
 if {[dict exists $::wires $pin]} {return [dict get $::wires $pin]}
 return {}
}
proc hbm_audit::clock {pin} {return [dict get $::props [file dirname $pin] CLOCK]}
proc hbm_audit::fanin {pin} {return [dict get $::fanins $pin]}
proc fixture {} {
 set ::props {}; set ::missing {}; set ::wires {}; set ::fanins {}
 foreach {cell type} {B BUFGCE BOARD BUFGCE VCC VCC GND GND F FDCE NAND LUT2} {
  dict set ::props $cell REF_NAME $type
 }
 dict set ::props NAND INIT "4'h7"
 dict set ::wires sink/C B/O
 dict set ::wires B/I BOARD/O
 dict set ::wires B/CE VCC/P
 dict set ::wires BOARD/CE VCC/P
 dict set ::wires plain/CLR F/Q
 set ::apb_stem wrangler/nodeOut_reset_catcher_1/io_sync_reset_chain/output_chain
 set ::axi_stem wrangler/nodeOut_reset_catcher/io_sync_reset_chain/output_chain
 foreach stem [list $::apb_stem $::axi_stem] clk {APB DUT} {
  for {set i 0} {$i < 3} {incr i} {
   set ff $stem/sync_${i}_reg
   dict set ::props $ff REF_NAME FDCE
   dict set ::props $ff INIT "1'b0"
   dict set ::props $ff CLOCK $clk
   dict set ::wires $ff/CE VCC/P
   dict set ::wires $ff/CLR ROOT/O
   if {$i == 2} {dict set ::wires $ff/D VCC/P} else {dict set ::wires $ff/D $stem/sync_[expr {$i+1}]_reg/Q}
  }
 }
 dict set ::wires target/CLR NAND/O
 dict set ::wires NAND/I0 $::apb_stem/sync_0_reg/Q
 dict set ::wires NAND/I1 $::axi_stem/sync_0_reg/Q
 dict set ::fanins target/CLR [lsort [list $::apb_stem/sync_0_reg/Q $::axi_stem/sync_0_reg/Q]]
}
set count 0
proc expect {label should_fail mutation command} {
 fixture
 uplevel 1 $mutation
 set failed [catch {uplevel 1 $command} result]
 if {$failed != $should_fail} {error "Unexpected acceptance result for $label: failed=$failed message=$result"}
 incr ::count
 puts "ok $label rejected=$failed"
}
set buffer {hbm_audit::checked_buffer B $trace buffer}
set shared {hbm_audit::transparent_connection sink/C B/O $trace shared}
set chain {hbm_audit::transparent_connection sink/C BOARD/O $trace chain}
set common {hbm_audit::common_reset target/CLR APB DUT $trace}
expect valid_bufgce 0 {} $buffer
expect valid_shared_buffer 0 {} $shared
expect valid_two_buffer_chain 0 {} $chain
expect valid_direct_ff_reset 0 {} {hbm_audit::transparent_connection plain/CLR F/Q $trace ff}
expect inverted_clock_pin 1 {dict set ::props B/I IS_INVERTED 1} $buffer
expect inverted_enable_pin 1 {dict set ::props B/CE IS_INVERTED true} $buffer
expect empty_polarity 1 {dict set ::props B/I IS_INVERTED {}} $buffer
expect missing_polarity 1 {dict set ::missing B/I 1} $buffer
expect enable_ground 1 {dict set ::wires B/CE GND/G} $buffer
expect nonbuffer_primitive 1 {dict set ::props B REF_NAME LUT2} $buffer
expect shared_buffer_cannot_skip_enable 1 {dict set ::wires B/CE GND/G} $shared
expect shared_buffer_cannot_skip_polarity 1 {dict set ::props B/I IS_INVERTED 1} $shared
expect board_buffer_cannot_skip_enable 1 {dict set ::wires BOARD/CE GND/G} $chain
expect valid_release_nand 0 {} $common
expect same_fanin_wrong_or_truth 1 {dict set ::props NAND INIT "4'he"} $common
expect same_fanin_wrong_nor_truth 1 {dict set ::props NAND INIT "4'h1"} $common
expect inverted_common_lut_input 1 {dict set ::props NAND/I0 IS_INVERTED 1} $common
expect wrong_release_clock 1 {dict set ::props $::axi_stem/sync_0_reg CLOCK APB} $common
expect release_init_one 1 {dict set ::props $::apb_stem/sync_0_reg INIT "1'b1"} $common
expect release_wrong_stage_order 1 {dict set ::wires $::apb_stem/sync_1_reg/D $::apb_stem/sync_0_reg/Q} $common
expect release_wrong_async_clear 1 {dict set ::wires $::apb_stem/sync_1_reg/CLR OTHER/O} $common
expect release_enable_not_constant 1 {dict set ::wires $::apb_stem/sync_1_reg/CE GND/G} $common
expect release_enable_inverted 1 {dict set ::props $::apb_stem/sync_1_reg/CE IS_INVERTED 1} $common
expect release_output_inverted 1 {dict set ::props $::apb_stem/sync_0_reg/Q IS_INVERTED 1} $common
expect release_not_filled_with_one 1 {dict set ::wires $::apb_stem/sync_2_reg/D GND/G} $common
close $trace
puts "checks_completed=$count"
