# Frozen shell scripts and fresh IP/RTL per variant. No checkpoints reused.
set runroot [file normalize $::env(A110_OUT)]
set scriptdir [file join $runroot shell/xilinx/common/tcl]
set_param general.maxThreads 4
source [file join $scriptdir prologue.tcl]
source [file join $scriptdir init.tcl]
source [file join $scriptdir synth.tcl]
file mkdir [file join $wrkdir audit]
report_timing_summary -file [file join $wrkdir audit post_synth_timing.txt] -max_paths 20
report_utilization -hierarchical -file [file join $wrkdir audit post_synth_utilization.txt]
source [file join $scriptdir opt.tcl]
source [file join $scriptdir place.tcl]
set reset_audit_dir [file join $wrkdir reset_replication_audit]
source [file join [file dirname [info script]] reset_opt.tcl]
write_checkpoint -force [file join $wrkdir post_reset_replication.dcp]
source [file join $scriptdir route.tcl]
set fd [open [file join $wrkdir audit postroute_checkpoint.sha256] w]
puts $fd [lindex [exec sha256sum -- [file join $wrkdir post_route.dcp]] 0]
close $fd
set audit_failures {}
if {[catch {
 set reset_postroute [reset_audit::snapshot $reset_audit_dir/postroute.dict]
 reset_audit::compare $reset_before $reset_postroute $reset_audit_dir/postroute_equivalence.txt
 reset_audit::timing $reset_audit_dir/postroute_timing 1
} audit_error]} {lappend audit_failures "reset: $audit_error"}
report_utilization -hierarchical -file $reset_audit_dir/utilization_hierarchical.rpt
report_utilization -slr -file $reset_audit_dir/utilization_slr.rpt
set dut [get_clocks clk_out1_harnessSysPLL]
if {[llength $dut] != 1} {error "Expected exactly one DUT clock"}
if {abs([get_property PERIOD $dut] - (1000.0 / 110.0)) > 0.001} {error "Expected DUT period 1000/110 ns"}
report_bus_skew -file [file join $wrkdir audit bus_skew.txt]
report_timing -from $dut -to $dut -delay_type max -max_paths 100 -nworst 1 -input_pins -nets -file [file join $wrkdir audit dut_setup_top100.txt]
report_timing -from $dut -to $dut -delay_type min -max_paths 100 -nworst 1 -input_pins -nets -file [file join $wrkdir audit dut_hold_top100.txt]
report_high_fanout_nets -timing -load_types -max_nets 200 -file [file join $wrkdir audit fanout_top200.txt]
report_clock_interaction -file [file join $wrkdir audit clock_interaction.txt]
report_cdc -details -file [file join $wrkdir audit cdc.txt]
check_timing -verbose -file [file join $wrkdir audit check_timing.txt]
report_exceptions -file [file join $wrkdir audit exceptions.txt]
source [file join [file dirname [info script]] hbm_audit.tcl]
if {[catch {hbm_audit::run [file join $wrkdir hbm_audit]} audit_error]} {
 lappend audit_failures "HBM: $audit_error"
}
set fd [open [file join $wrkdir audit audit_status.txt] w]
puts $fd "failures=$audit_failures"
close $fd
set fd [open [file join $wrkdir audit dsp_registers.tsv] w]
puts $fd "cell\tAREG\tBREG\tCREG\tMREG\tPREG"
foreach cell [get_cells -hier -filter {REF_NAME == DSP48E2}] {
  puts $fd "$cell\t[get_property AREG $cell]\t[get_property BREG $cell]\t[get_property CREG $cell]\t[get_property MREG $cell]\t[get_property PREG $cell]"
}
close $fd
set fd [open [file join $wrkdir audit run_properties.txt] w]
puts $fd "part=[get_property PART [current_design]]"
puts $fd "dut_period=[get_property PERIOD $dut]"
puts $fd "dut_setup_wns=[get_property SLACK [get_timing_paths -from $dut -to $dut -delay_type max]]"
puts $fd "dut_hold_whs=[get_property SLACK [get_timing_paths -from $dut -to $dut -delay_type min]]"
close $fd
# Reports are kept even for a timing failure. Acceptance is strict WNS/WHS >= 0,
# not the legacy report.tcl's -0.100ns exit threshold.
source [file join $scriptdir report.tcl]

source [file join [file dirname [info script]] check_timing.tcl]
if {[llength $audit_failures]} {error "Physical structural audit failed: $audit_failures"}
exit
