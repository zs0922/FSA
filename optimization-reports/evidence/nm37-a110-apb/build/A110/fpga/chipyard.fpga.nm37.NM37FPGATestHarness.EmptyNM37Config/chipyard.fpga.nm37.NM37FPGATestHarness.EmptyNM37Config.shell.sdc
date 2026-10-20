# ------------------------- Base Clocks --------------------
create_clock -name sys_clock -period 10.0 [get_ports {sys_clock_p}]
set_input_jitter sys_clock 0.5
create_clock -name xdma_ref_clk -period 10.0 [get_ports {xdma_refclk_p}]
set_input_jitter xdma_ref_clk 0.5
# ------------------------- Clock Groups -------------------
set_clock_groups -asynchronous \
  -group [list [get_clocks -of_objects [get_pins { \
      harnessSysPLL/clk_out1 \
      harnessSysPLL/clk_out2 \
    }]]] \
  -group [list [get_clocks { \
      xdma_ref_clk \
    }] [get_clocks -of_objects [get_pins { \
      pcie/blackbox/axi_aclk \
    }]]]
# ------------------------- False Paths --------------------
set_false_path -through [get_pins {pcie/blackbox/axi_aresetn}]
set_false_path -through [get_ports {prst_n}]
set_false_path -through [get_pins {powerOnReset_fpga_power_on/power_on_reset}]
# ------------------------- IO Timings ---------------------

