## P1 MAX_FANOUT constraint
## Reduces fanout on critical control signals to improve timing
set_property MAX_FANOUT 64 [get_cells -hierarchical dutDomain/fsa/fsa/mxControl/fsm_list_0/_mxControl_io_acc_ctrl_bits_cmd]
set_property MAX_FANOUT 64 [get_cells -hierarchical dutDomain/fsa/fsa/mxControl/fsm_list_1/accumTimer_reg]