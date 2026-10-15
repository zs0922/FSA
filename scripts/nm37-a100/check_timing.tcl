# Strict setup/hold guard for the DUT and every timed clock group.
set dut [get_clocks clk_out1_harnessSysPLL]
if {[llength $dut] != 1 || abs([get_property PERIOD $dut] - 10.0) > 0.001} {
    error "Expected exactly one 100 MHz DUT clock"
}
foreach kind {max min} {
    set paths [get_timing_paths -delay_type $kind -max_paths 1]
    if {[llength $paths] == 0 || [get_property SLACK $paths] < 0} { error "Global $kind timing failed" }
    set paths [get_timing_paths -from $dut -to $dut -delay_type $kind -max_paths 1]
    if {[llength $paths] == 0 || [get_property SLACK $paths] < 0} { error "DUT $kind timing failed" }
}
