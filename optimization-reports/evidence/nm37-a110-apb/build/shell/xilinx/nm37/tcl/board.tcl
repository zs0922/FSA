set name {nm37}
# The NM37 board carries an xcvu37p_CIV (engineering-sample silicon, different
# JTAG IDCODE than production xcvu37p). The bitstream must be built for the
# CIV part or Labtools rejects it ("Incorrect bitstream assigned to device").
set part_fpga {xcvu37p_CIV-fsvh2892-2L-e}
set part_board {}
