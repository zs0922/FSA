
create_ip -name hbm -vendor xilinx.com -library ip -version 1.0 -module_name testxilinx_hbm
set_property -dict [list \
    CONFIG.USER_APB_EN {false} \
    CONFIG.USER_APB_PCLK_0 {55} \
    CONFIG.USER_CLK_SEL_LIST0 {AXI_00_ACLK} \
    CONFIG.USER_CLK_SEL_LIST1 {AXI_16_ACLK} \
    CONFIG.USER_HBM_DENSITY {8GB} \
    CONFIG.USER_HBM_STACK {1} \
    CONFIG.USER_MC_ENABLE_APB_01 {FALSE} \
    CONFIG.USER_SWITCH_ENABLE_01 {FALSE} \
    CONFIG.USER_XSDB_INTF_EN {FALSE} ] \
[get_ips testxilinx_hbm]
