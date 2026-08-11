open_checkpoint /home/zhangsi/chipyard-fsa/fpga/generated-src/chipyard.fpga.u280.U280FPGATestHarness.EmptyU280Config/obj/post_route.dcp

puts "=== HBM Calibration Signals ==="
set calib_nets [get_nets -hier -filter {NAME =~ "*calib*" || NAME =~ "*CALIB*"}]
foreach sig $calib_nets {
    puts "  $sig"
}
puts "Total calib nets: [llength $calib_nets]"

puts ""
puts "=== HBM Init/Done Signals ==="
set init_nets [get_nets -hier -filter {NAME =~ "*init*" || NAME =~ "*INIT*" || NAME =~ "*done*" || NAME =~ "*DONE*"}]
foreach sig $init_nets {
    puts "  $sig"
}

puts ""
puts "=== HBM IP Pins ==="
set hbm_pins [get_pins -hier -filter {NAME =~ "*hbm*" && (NAME =~ "*calib*" || NAME =~ "*init*" || NAME =~ "*done*" || NAME =~ "*ready*" || NAME =~ "*status*")}]
foreach pin $hbm_pins {
    puts "  $pin"
}

puts ""
puts "=== HBM Cells ==="
set hbm_cells [get_cells -hier -filter {NAME =~ "*hbm*" && REF_NAME =~ "hbm*"}]
foreach cell $hbm_cells {
    puts "  $cell (ref: [get_property REF_NAME $cell])"
}

close_checkpoint
