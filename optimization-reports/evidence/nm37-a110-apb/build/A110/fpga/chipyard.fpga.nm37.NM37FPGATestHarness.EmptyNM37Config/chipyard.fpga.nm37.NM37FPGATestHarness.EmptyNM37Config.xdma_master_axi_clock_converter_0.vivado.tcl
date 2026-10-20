create_ip -name axi_clock_converter \
          -vendor xilinx.com \
          -library ip \
          -version 2.1 \
          -module_name xdma_master_axi_clock_converter_0

set_property -dict [list CONFIG.PROTOCOL {AXI4} \
                         CONFIG.ADDR_WIDTH {34} \
                         CONFIG.SYNCHRONIZATION_STAGES {3} \
                         CONFIG.DATA_WIDTH {256} \
                         CONFIG.ID_WIDTH {4} \
                         CONFIG.AWUSER_WIDTH {0} \
                         CONFIG.ARUSER_WIDTH {0} \
                         CONFIG.RUSER_WIDTH {0} \
                         CONFIG.WUSER_WIDTH {0} \
                         CONFIG.BUSER_WIDTH {0}] \
             [get_ips xdma_master_axi_clock_converter_0]
