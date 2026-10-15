# Source after existing place.tcl, before route.tcl. Requires reset_audit_dir.
if {![info exists reset_audit_dir]} {error "Set reset_audit_dir to fresh evidence directory"}
file mkdir $reset_audit_dir
source [file join [file dirname [info script]] reset_audit.tcl]
set reset_before [reset_audit::snapshot $reset_audit_dir/before.dict]
set reset_nets [dict get $reset_before fabric_nets]
if {![llength $reset_nets]} {error "No direct fabric reset branch selected; refusing silent no-op"}
phys_opt_design -force_replication_on_nets [get_nets $reset_nets]
set reset_after_rep [reset_audit::snapshot $reset_audit_dir/after_replication.dict]
reset_audit::compare $reset_before $reset_after_rep $reset_audit_dir/replication_equivalence.txt
if {[dict size [dict get $reset_after_rep cells]] <= [dict size [dict get $reset_before cells]]} {error "Forced replication created no additional matching final-stage FDCE; inspect reports"}
# First candidate intentionally uses force replication only.
# Global -slr_crossing_opt is a separate, unexecuted fallback experiment.
reset_audit::coverage $reset_audit_dir/post_replication_timing 0
# Caller must repeat snapshot/compare after route and call reset_audit::timing.
