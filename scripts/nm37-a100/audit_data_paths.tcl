# Read-only post-route timing queries. Run only after the implementation exits:
# vivado -mode batch -source audit_data_paths.tcl -tclargs POST_ROUTE_DCP NEW_OUTPUT_DIR
# Optional third argument selects the DUT clock (default below).
# No netlist, placement, routing, constraints or checkpoint is changed/saved.
# Reference: AMD UG835 all_registers / report_timing command documentation.
# https://docs.amd.com/r/2021.2-English/ug835-vivado-tcl-commands/report_timing

if {$argc < 2 || $argc > 3} {
    error "Expected POST_ROUTE_DCP NEW_OUTPUT_DIR ?DUT_CLOCK?"
}
set audit_dcp [file normalize [lindex $argv 0]]
set audit_dir [file normalize [lindex $argv 1]]
set audit_clock_name clk_out1_harnessSysPLL
if {$argc == 3} { set audit_clock_name [lindex $argv 2] }
if {![file isfile $audit_dcp] || [file extension $audit_dcp] ne ".dcp"} {
    error "Checkpoint must be an existing .dcp file: $audit_dcp"
}
if {[file exists $audit_dir]} {
    error "Use a new output directory to preserve existing audit evidence: $audit_dir"
}
file mkdir $audit_dir
set_param general.maxThreads 4
open_checkpoint $audit_dcp

proc require_objects {label objects} {
    set n [llength $objects]
    if {$n == 0} { error "Empty mapped-object query: $label" }
    puts "$label: $n objects"
    return $objects
}
set audit_clock [require_objects dut_clock [get_clocks -quiet $audit_clock_name]]
if {[llength $audit_clock] != 1} { error "Expected exactly one DUT clock" }
set dut_regs [require_objects dut_registers [all_registers -clock $audit_clock -cells]]
set excluded_regs [require_objects reset_catcher_sources [filter $dut_regs {NAME =~ *reset_catcher*}]]
set dut_nonreset [require_objects nonreset_registers [filter $dut_regs {NAME !~ *reset_catcher*}]]

# Explicit get_cells checks validate the mapped hierarchy before scoped queries.
# Leaf register names are preserved in the actual A100 post-route timing report.
set pe_cells [require_objects pe_mapped_flops [get_cells -quiet -hierarchical -filter {NAME =~ dutDomain/fsa/fsa/sa/mesh_* && REF_NAME =~ FD*}]]
set cmp_cells [require_objects cmp_mapped_flops [get_cells -quiet -hierarchical -filter {NAME =~ dutDomain/fsa/fsa/sa/cmp_array_* && REF_NAME =~ FD*}]]
set sp_mem_cells [require_objects scratchpad_mapped_memory [get_cells -quiet -hierarchical -filter {NAME =~ dutDomain/fsa/fsa/spRAM_sram/* && REF_NAME =~ RAM*}]]
set pe_regs [require_objects pe_clocked_registers [filter $dut_nonreset {NAME =~ dutDomain/fsa/fsa/sa/mesh_*}]]
set cmp_regs [require_objects cmp_clocked_registers [filter $dut_nonreset {NAME =~ dutDomain/fsa/fsa/sa/cmp_array_*}]]

# Scope the two sides of each real after-add cut separately. These also include
# paths controlled by non-reset command/control registers; classify actual
# report sources rather than labelling every path into an FMA as arithmetic.
set pe_add_regs [require_objects pe_after_add_registers [filter $pe_regs {NAME =~ */macUnit/mulAddExp2/fma/adderOutAbs_reg*}]]
set cmp_add_regs [require_objects cmp_after_add_registers [filter $cmp_regs {NAME =~ */cmpUnit/fma/rawFMA/adderOutAbs_reg*}]]
set cmp_max_regs [require_objects cmp_max_feedback_registers [filter $cmp_regs {NAME =~ */newMax_*_reg*}]]

# BRAM data outputs (DO*) and distributed RAM outputs (O*) cover both A100
# and the old A mapping. Use -through, preserving the full clocked path and
# including address-register -> RAM data -> consumer paths for distributed RAM.
set sp_data_pins [require_objects scratchpad_memory_data_outputs [get_pins -quiet -of_objects $sp_mem_cells -filter {DIRECTION == OUT && (REF_PIN_NAME =~ DO* || REF_PIN_NAME =~ O*)}]]

set scope [open [file join $audit_dir scope.txt] w]
puts $scope "checkpoint=$audit_dcp"
puts $scope "clock=$audit_clock_name"
puts $scope "clock_period_ns=[get_property PERIOD $audit_clock]"
puts $scope "excluded_source_and_target_pattern=*reset_catcher*"
puts $scope "Only DUT-clock sequential primitives returned by all_registers are queried. Primary input/output paths and other clock domains are outside this scope."
puts $scope "Non-reset means only reset_catcher primitives are excluded; other reset/control/data sources remain eligible."
puts $scope "Reports contain worst paths within each named query, not exhaustive path enumeration."
puts $scope "PE/CMP hierarchy queries include local control and data registers. After-add queries target or launch at the exact adderOutAbs register banks."
puts $scope "Scratchpad data paths must traverse DO*/O* output pins of mapped RAM* primitives under spRAM_sram; both BRAM and distributed RAM mapping are supported."
foreach name {dut_regs excluded_regs dut_nonreset pe_cells cmp_cells sp_mem_cells pe_regs cmp_regs pe_add_regs cmp_add_regs cmp_max_regs sp_data_pins} {
    puts $scope "$name.count=[llength [set $name]]"
}
close $scope

proc scoped_report {dir name sources targets limit {through {}}} {
    set cmd [list report_timing -from $sources -to $targets -delay_type max -max_paths $limit -nworst 1 -input_pins -nets -file [file join $dir ${name}.txt]]
    if {[llength $through]} { lappend cmd -through $through }
    {*}$cmd
}
scoped_report $audit_dir nonreset_top100 $dut_nonreset $dut_nonreset 100
scoped_report $audit_dir pe_from_top20 $pe_regs $dut_nonreset 20
scoped_report $audit_dir pe_to_top20 $dut_nonreset $pe_regs 20
scoped_report $audit_dir pe_after_add_in_top20 $dut_nonreset $pe_add_regs 20
scoped_report $audit_dir pe_after_add_out_top20 $pe_add_regs $dut_nonreset 20
scoped_report $audit_dir cmp_from_top20 $cmp_regs $dut_nonreset 20
scoped_report $audit_dir cmp_to_top20 $dut_nonreset $cmp_regs 20
scoped_report $audit_dir cmp_after_add_in_top20 $dut_nonreset $cmp_add_regs 20
scoped_report $audit_dir cmp_after_add_out_top20 $cmp_add_regs $dut_nonreset 20
scoped_report $audit_dir cmp_max_feedback_top20 $dut_nonreset $cmp_max_regs 20
scoped_report $audit_dir scratchpad_data_top20 $dut_nonreset $dut_nonreset 20 $sp_data_pins
puts "Read-only mapped data/control timing audit complete: $audit_dir"
close_design
