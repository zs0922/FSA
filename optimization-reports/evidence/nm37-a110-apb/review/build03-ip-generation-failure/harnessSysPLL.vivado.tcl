create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name \
 harnessSysPLL -dir $ipdir -force
set_property -dict [list \
 CONFIG.CLK_IN1_BOARD_INTERFACE {Custom} \
 CONFIG.PRIM_SOURCE {No_buffer} \
 CONFIG.NUM_OUT_CLKS {2} \
 CONFIG.PRIM_IN_FREQ {100.0} \
 CONFIG.CLKIN1_JITTER_PS {50.0} \
 CONFIG.CLKOUT1_USED {true} \
 CONFIG.CLKOUT2_USED {true} \
 CONFIG.CLKOUT3_USED {false} \
 CONFIG.CLKOUT4_USED {false} \
 CONFIG.CLKOUT5_USED {false} \
 CONFIG.CLKOUT6_USED {false} \
 CONFIG.CLKOUT7_USED {false} \
 CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {110.0} \
 CONFIG.CLKOUT1_REQUESTED_PHASE {0.0} \
 CONFIG.CLKOUT1_REQUESTED_DUTY_CYCLE {50.0} \
 CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {55.0} \
 CONFIG.CLKOUT2_REQUESTED_PHASE {0.0} \
 CONFIG.CLKOUT2_REQUESTED_DUTY_CYCLE {50.0} \
] [get_ips harnessSysPLL]
set mult [get_property CONFIG.MMCM_CLKFBOUT_MULT_F [get_ips harnessSysPLL]]
set div1 [get_property CONFIG.MMCM_DIVCLK_DIVIDE [get_ips harnessSysPLL]]
set jitter [get_property CONFIG.CLKOUT1_JITTER [get_ips harnessSysPLL]]
if {$jitter > 230.0} {
  puts "Output jitter $jitter ps exceeds required limit of 230.0"
  exit 1
}
set phase [get_property CONFIG.MMCM_CLKOUT0_PHASE [get_ips harnessSysPLL]]
if {$phase < -5.0 || $phase > 5.0} {
  puts "Achieved phase $phase degrees is outside tolerated range -5.0-5.0"
  exit 1
}
set div2 [get_property CONFIG.MMCM_CLKOUT0_DIVIDE_F [get_ips harnessSysPLL]]
set freq [expr { 100.0 * $mult / $div1 / $div2 }]
if {$freq < 108.9 || $freq > 111.1} {
  puts "Achieved frequency $freq MHz is outside tolerated range 108.9-111.1"
  exit 1
}
puts "Achieve frequency $freq MHz phase $phase degrees jitter $jitter ps"
set jitter [get_property CONFIG.CLKOUT2_JITTER [get_ips harnessSysPLL]]
if {$jitter > 200.0} {
  puts "Output jitter $jitter ps exceeds required limit of 200.0"
  exit 1
}
set phase [get_property CONFIG.MMCM_CLKOUT1_PHASE [get_ips harnessSysPLL]]
if {$phase < -5.0 || $phase > 5.0} {
  puts "Achieved phase $phase degrees is outside tolerated range -5.0-5.0"
  exit 1
}
set div2 [get_property CONFIG.MMCM_CLKOUT1_DIVIDE [get_ips harnessSysPLL]]
set freq [expr { 100.0 * $mult / $div1 / $div2 }]
if {$freq < 54.45 || $freq > 55.55} {
  puts "Achieved frequency $freq MHz is outside tolerated range 54.45-55.55"
  exit 1
}
puts "Achieve frequency $freq MHz phase $phase degrees jitter $jitter ps"
