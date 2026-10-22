# Export only a strictly accepted 70 MHz routed checkpoint.
if {$argc != 2} {error "Expected POST_ROUTE_DCP NEW_OUTPUT_DIR"}
lassign $argv dcp out
set dcp [file normalize $dcp]
set out [file normalize $out]
if {![file isfile $dcp] || [file exists $out]} {error "Expected existing DCP and new output directory"}
file mkdir $out
set_param general.maxThreads 4
open_checkpoint $dcp
source [file join [file dirname [info script]] check_timing.tcl]
report_timing_summary -file [file join $out timing.txt]
report_drc -file [file join $out drc.txt]
set bad [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]
if {[llength $bad]} {error "DRC errors or critical warnings prohibit export: $bad"}
set_property BITSTREAM.Config.SPI_buswidth 8 [current_design]
write_bitstream [file join $out NM37_A70.bit]
close_design
