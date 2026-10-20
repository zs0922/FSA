# Read-only reports from the exact original routed checkpoint.
# No synth/opt/place/route, constraint edits, or checkpoint writes are permitted.
if {$argc != 5} {error "Expected DCP NEW_OBJ FROZEN_DRIVERS ORIGINAL_OBJ REVISED_AUDITOR"}
lassign $argv dcp wrkdir frozen original_obj auditor
foreach name {dcp wrkdir frozen original_obj auditor} {set $name [file normalize [set $name]]}
set_param general.maxThreads 4
open_checkpoint $dcp
foreach dir {report audit hbm_audit} {
 if {[file exists $wrkdir/$dir]} {error "New audit report directory already exists: $wrkdir/$dir"}
 file mkdir $wrkdir/$dir
}
set reset_dir $wrkdir/reset_replication_audit
set audit_failures {}
source $frozen/scripts/nm37-a110/reset_audit.tcl
if {[catch {
 set f [open $reset_dir/before.dict r]
 set before [read $f]
 close $f
 dict size $before
 set routed [reset_audit::snapshot $reset_dir/postroute.dict]
 reset_audit::compare $before $routed $reset_dir/postroute_equivalence.txt
 reset_audit::timing $reset_dir/postroute_timing 1
} message]} {lappend audit_failures "reset: $message"}
report_utilization -hierarchical -file $reset_dir/utilization_hierarchical.rpt
report_utilization -slr -file $reset_dir/utilization_slr.rpt
set dut [get_clocks -quiet clk_out1_harnessSysPLL]
if {[llength $dut] != 1 || abs([get_property PERIOD $dut]-1000.0/110.0)>0.001} {
 error "Opened routed DCP lacks the expected 110 MHz DUT clock"
}
report_bus_skew -file $wrkdir/audit/bus_skew.txt
report_timing -from $dut -to $dut -delay_type max -max_paths 100 -nworst 1 -input_pins -nets -file $wrkdir/audit/dut_setup_top100.txt
report_timing -from $dut -to $dut -delay_type min -max_paths 100 -nworst 1 -input_pins -nets -file $wrkdir/audit/dut_hold_top100.txt
report_high_fanout_nets -timing -load_types -max_nets 200 -file $wrkdir/audit/fanout_top200.txt
report_clock_interaction -file $wrkdir/audit/clock_interaction.txt
report_cdc -details -file $wrkdir/audit/cdc.txt
check_timing -verbose -file $wrkdir/audit/check_timing.txt
report_exceptions -file $wrkdir/audit/exceptions.txt
source $auditor/hbm_audit.tcl
if {[catch {hbm_audit::run $wrkdir/hbm_audit} message]} {lappend audit_failures "HBM: $message"}
if {[catch {
 set apb_primitive [hbm_audit::one [get_cells -quiet -hier -filter {REF_NAME == HBM_SNGLBLI_INTF_APB}] "HBM APB primitive"]
 set apb_pin [hbm_audit::pin $apb_primitive PCLK]
 set apb_clock [hbm_audit::clock $apb_pin]
 report_pulse_width -clocks $apb_clock -limit 100 -significant_digits 3 -file $wrkdir/hbm_audit/apb_pulse_width.rpt $apb_pin
} message]} {lappend audit_failures "APB pulse report: $message"}
set f [open $wrkdir/audit/run_properties.txt w]
puts $f "part=[get_property PART [current_design]]"
puts $f "dut_period=[get_property PERIOD $dut]"
puts $f "dut_setup_wns=[get_property SLACK [get_timing_paths -from $dut -to $dut -delay_type max]]"
puts $f "dut_hold_whs=[get_property SLACK [get_timing_paths -from $dut -to $dut -delay_type min]]"
close $f
set f [open $wrkdir/audit/dsp_registers.tsv w]
puts $f "cell\tAREG\tBREG\tCREG\tMREG\tPREG"
foreach cell [get_cells -hier -filter {REF_NAME == DSP48E2}] {
 puts $f "$cell\t[get_property AREG $cell]\t[get_property BREG $cell]\t[get_property CREG $cell]\t[get_property MREG $cell]\t[get_property PREG $cell]"
}
close $f
# Mirror every final report from the frozen shell without its legacy -0.100 ns
# exit threshold; the original strict checker below remains authoritative.
report_datasheet -file $wrkdir/report/datasheet.txt
report_utilization -hierarchical -file $wrkdir/report/utilization.txt
report_clock_utilization -file $wrkdir/report/utilization.txt -append
report_ram_utilization -file $wrkdir/report/utilization.txt -append -detail
report_utilization -file $wrkdir/report/utilization_flat.txt
report_utilization -slr -file $wrkdir/report/utilization_slr.txt
report_timing_summary -file $wrkdir/report/timing.txt -max_paths 10
report_high_fanout_nets -file $wrkdir/report/fanout.txt -timing -load_types -max_nets 25
report_drc -file $wrkdir/report/drc.txt
report_io -file $wrkdir/report/io.txt
report_clocks -file $wrkdir/report/clocks.txt
report_power -file $wrkdir/report/power.txt -format both
report_design_analysis -congestion -file $wrkdir/report/congestion.txt
report_design_analysis -complexity -file $wrkdir/report/complexity.txt
report_route_status -file $wrkdir/report/route_status.txt
if {[catch {source $frozen/scripts/nm37-a110/check_timing.tcl} message]} {
 lappend audit_failures "strict timing: $message"
}
set f [open $wrkdir/audit/audit_status.txt w]
puts $f "failures=$audit_failures"
close $f
close_design
if {[llength $audit_failures]} {error "Revised physical audit failed: $audit_failures"}
exit
