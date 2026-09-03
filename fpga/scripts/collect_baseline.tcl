# collect_baseline.tcl - Post-route baseline report collection
# Run after route_design completes (before or after bitstream).
# Usage: vivado -mode batch -source collect_baseline.tcl
#   or sourced from within an existing Vivado session.

set rptdir [file join [pwd] report]
file mkdir $rptdir

puts "=== Collecting baseline reports ==="

# --- Power ---
report_power -file [file join $rptdir power.txt] -format both
report_power -file [file join $rptdir power_verbose.txt] -verbose

# --- Utilization (per-SLR) ---
report_utilization -slr -file [file join $rptdir utilization_slr.txt]
report_utilization -hierarchical -file [file join $rptdir utilization_hierarchical.txt]

# --- Congestion / Design Analysis ---
report_design_analysis -congestion -file [file join $rptdir congestion.txt]
report_design_analysis -complexity -file [file join $rptdir complexity.txt]
report_design_analysis -timing -file [file join $rptdir design_analysis_timing.txt]

# --- Route status ---
report_route_status -file [file join $rptdir route_status.txt]

# --- Long wires ---
report_design_analysis -long_wires -file [file join $rptdir long_wires.txt]

puts "=== Baseline reports written to $rptdir ==="
