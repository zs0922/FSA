# Export the accepted routed design; do not rerun synthesis/place/route.
# vivado -mode batch -source THIS_FILE -tclargs POST_ROUTE_DCP NEW_OUTPUT_DIR
if {$argc != 2} { error "Expected POST_ROUTE_DCP NEW_OUTPUT_DIR" }
set dcp [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
if {![file isfile $dcp]} { error "Missing checkpoint: $dcp" }
if {[file exists $out]} { error "Output directory already exists: $out" }
file mkdir $out
set_param general.maxThreads 4
open_checkpoint $dcp
source [file join [file dirname [info script]] ../../scripts/nm37-a110/check_timing.tcl]
set clk [get_clocks clk_out1_harnessSysPLL]
if {[llength $clk] != 1} { error "Expected one DUT clock" }
if {abs([get_property PERIOD $clk] - (1000.0 / 110.0)) > 0.001} { error "DUT is not 110 MHz" }
if {[get_property SLACK [get_timing_paths -from $clk -to $clk -delay_type max]] < 0} { error "DUT setup violation" }
if {[get_property SLACK [get_timing_paths -from $clk -to $clk -delay_type min]] < 0} { error "DUT hold violation" }
report_timing_summary -file [file join $out timing.txt]
report_drc -file [file join $out drc.txt]
set_property BITSTREAM.Config.SPI_buswidth 8 [current_design]
write_bitstream [file join $out NM37_A110.bit]
close_design
