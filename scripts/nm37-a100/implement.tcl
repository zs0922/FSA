# Frozen shell scripts and fresh IP/RTL per variant. No checkpoints reused.
set runroot [file normalize $::env(A100_OUT)]
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
source [file join $scriptdir route.tcl]
set dut [get_clocks clk_out1_harnessSysPLL]
if {[llength $dut] != 1} {error "Expected exactly one DUT clock"}
if {abs([get_property PERIOD $dut] - 10.0) > 0.001} {error "Expected DUT period 10 ns"}
report_bus_skew -file [file join $wrkdir audit bus_skew.txt]
report_timing -from $dut -to $dut -delay_type max -max_paths 100 -nworst 1 -input_pins -nets -file [file join $wrkdir audit dut_setup_top100.txt]
report_timing -from $dut -to $dut -delay_type min -max_paths 100 -nworst 1 -input_pins -nets -file [file join $wrkdir audit dut_hold_top100.txt]
report_high_fanout_nets -timing -load_types -max_nets 200 -file [file join $wrkdir audit fanout_top200.txt]
report_clock_interaction -file [file join $wrkdir audit clock_interaction.txt]
report_cdc -details -file [file join $wrkdir audit cdc.txt]
check_timing -verbose -file [file join $wrkdir audit check_timing.txt]
report_exceptions -file [file join $wrkdir audit exceptions.txt]
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
exit
