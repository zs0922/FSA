set_property -dict [list \
	CONFIG_VOLTAGE {1.8} \
	CFGBVS {GND} \
	] [current_design]

## M1 §4.0 fanout fix: the accRAM write-address registers (ShiftRegister
## outputs in FSA.scala driving all accRAM banks' write ports, fanout 1216
## in the p3 report) are FDRE-driven, so synthesis-time MAX_FANOUT works
## here (unlike the LUT-driven cmd/timer nets, which needed RTL replication).
set_property MAX_FANOUT 128 [get_cells -hierarchical -filter {NAME =~ *sram_accRAM_fullWrite_0_addr_REG*}]
