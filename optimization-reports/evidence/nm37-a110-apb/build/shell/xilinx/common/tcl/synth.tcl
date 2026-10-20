# See LICENSE for license details.

# Read the specified list of IP files
read_ip [glob -directory $ipdir [file join * {*.xci}]]

# Include board-specific synthesis fixups if they exist
set fixup_file [file join $boarddir vsrc ${board}_fixups.sv]
if {[file exists $fixup_file]} {
  puts "Including board synthesis fixups: $fixup_file"
  read_verilog -sv $fixup_file
}

# Synthesize the design
synth_design -top $top -flatten_hierarchy rebuilt -directive PerformanceOptimized -retiming

# Checkpoint the current design
write_checkpoint -force [file join $wrkdir post_synth]
