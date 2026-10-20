# Additional independent real-routed query for the three changed CDC sources
# discovered in the final CDC report. No constraints/netlist/checkpoint writes.
set link_script [file normalize [info script]]
set link_root [file dirname $link_script]
set link_base [file dirname $link_root]
set link_board [file join $link_base build04 A110 fpga chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config]
set link_dcp [file join $link_board obj post_route.dcp]
set link_sha 44195817d21dfdc147800868637505f1ce89303fd3aa98f1c6e044f4b3ccf236
set link_auditor [file join $link_root reaudit-staging hbm_audit.tcl]
set link_queries [file join $link_root postroute-cdc-review.tcl]
set link_dir [file join $link_root build04-postroute-linkup-review02]
proc link_hash {p} {return [lindex [exec sha256sum -- $p] 0]}
if {[link_hash $link_dcp] ne $link_sha} {error "Unexpected routed DCP hash"}
if {[link_hash $link_auditor] ne "fab04cc7b93481f26e8fd799d50e31db08a80b1da576910035c49d8c9d62d139"} {error "Unexpected auditor helper"}
if {[link_hash $link_queries] ne "59e6e49e6f2f52a3d16343687e6a6b3f1b20357cef716cbe91abeddf509628e2"} {error "Unexpected query helper source"}
if {[file exists $link_dir]} {error "Refusing overwrite of independent link-up evidence"}
file mkdir $link_dir
set hashes [open [file join $link_dir input-sha256.tsv] w]
puts $hashes "sha256\tinput"
set link_inputs [list $link_script $link_dcp $link_auditor $link_queries \
 [file join $link_base build04 build.json] \
 [file join $link_root build04-postroute-cdc-review cdc.rpt] \
 [file join $link_root build04-postroute-cdc-review cdc-all-checks.rpt] \
 [file join $link_board obj ip nm37_xdma ip_0 source nm37_xdma_pcie4c_ip_pcie4c_uscale_core_top.v]]
set link_input_hashes [dict create]
foreach p $link_inputs {
 set digest [link_hash $p]
 puts $hashes "$digest\t$p"
 dict set link_input_hashes $p $digest
}
close $hashes
set props [open [file join $link_dir properties.txt] w]
puts $props "mode=independent-routed-link-up-cdc-evidence"
puts $props "candidate_physical_acceptance=false"
puts $props "checkpoint_sha256=$link_sha"
puts $props "started_utc=[clock format [clock seconds] -gmt true -format {%Y-%m-%dT%H:%M:%SZ}]"
flush $props
set opened false
set rc [catch {
 set_param general.maxThreads 2
 open_checkpoint $link_dcp
 set opened true
 update_timing
 source $link_auditor
 # Only reuse the already-hashed pure query namespace, never the runner.
 set file_fd [open $link_queries r]
 set source_text [read $file_fd]
 close $file_fd
 set first [string first "namespace eval routed_cdc \{" $source_text]
 set last [string first "\nset opened false" $source_text]
 if {$first < 0 || $last <= $first} {error "Query namespace delimiters missing"}
 set helper [string range $source_text $first [expr {$last - 1}]]
 if {![info complete $helper]} {error "Incomplete query helper"}
 eval $helper
 set routed_cdc::fd [open [file join $link_dir structure.tsv] w]
 set routed_cdc::cone_fd [open [file join $link_dir reset-cones.txt] w]
 set pci pcie/blackbox/inst/pcie4c_ip_i/inst
 set core [hbm_audit::one [get_cells -hier -filter {REF_NAME == PCIE4CE4}] "PCIe primitive"]
 set leaves [dict create \
 [routed_cdc::p {pcieResetControl/releaseSync/sync_reg[2]} Q] qualified \
 [routed_cdc::p pcieResetControl/released_reg Q] released \
 [routed_cdc::p powerOnReset_fpga_power_on/power_on_reset_reg Q] por \
 [hbm_audit::pin $core {CFGPHYLINKSTATUS[0]}] link0 \
 [hbm_audit::pin $core {CFGPHYLINKSTATUS[1]}] link1]
 set x0 [format {%s/user_lnk_up_cdc/arststages_ff_reg[0]} $pci]
 set x1 [format {%s/user_lnk_up_cdc/arststages_ff_reg[1]} $pci]
 foreach cell [list $x0 $x1 $pci/user_lnk_up_reg] {
  routed_cdc::check $cell {
   set ff [routed_cdc::reg $cell FDCE 1'b0 _pcie_io_clocks_axi_aclk]
   if {$cell ne "$pci/user_lnk_up_reg"} {
    routed_cdc::require {[hbm_audit::truth [get_property ASYNC_REG $ff]]} "Missing link-up XPM ASYNC_REG"
   }
   routed_cdc::vcc [routed_cdc::p $cell CE]
   set reset_pin [routed_cdc::p $cell CLR]
   routed_cdc::zero_polarity $reset_pin
   routed_cdc::cone $reset_pin
   routed_cdc::truth $reset_pin $leaves {qualified released por link0 link1} {$por || !$qualified || !$released || !$link0 || !$link1}
  }
 }
 routed_cdc::check link_up_release_chain {
  routed_cdc::vcc [routed_cdc::p $x0 D]
  routed_cdc::direct [routed_cdc::p $x1 D] [routed_cdc::p $x0 Q]
  routed_cdc::direct [routed_cdc::p $pci/user_lnk_up_reg D] [routed_cdc::p $x1 Q]
 }
 puts $routed_cdc::fd "structural_mismatches=[llength $routed_cdc::errors]"
 close $routed_cdc::fd
 close $routed_cdc::cone_fd
 puts $props "structural_mismatches=[llength $routed_cdc::errors]"
 foreach p $link_inputs {
  if {[link_hash $p] ne [dict get $link_input_hashes $p]} {error "Input changed: $p"}
 }
} message options]
if {$rc} {
 puts $props "report_generation=failed"
 puts $props "error=$message"
 set error_fd [open [file join $link_dir failure.txt] w]
 puts $error_fd [dict get $options -errorinfo]
 close $error_fd
} else {puts $props "report_generation=completed"}
puts $props "ended_utc=[clock format [clock seconds] -gmt true -format {%Y-%m-%dT%H:%M:%SZ}]"
close $props
if {$opened} {close_design}
if {$rc} {return -options $options $message}
puts "Independent routed link-up evidence: $link_dir"
exit
