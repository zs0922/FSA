# Recheck serialized routed reset evidence without requiring a Vivado session.
# Tcl dictionaries are parsed as data; none of their contents are evaluated.
if {$argc != 1} {error "Expected reset evidence directory"}
set dir [lindex $argv 0]
proc read_dict {path} {set f [open $path r]; set d [read $f]; close $f; dict size $d; return $d}
set before [read_dict $dir/before.dict]
set replicated [read_dict $dir/after_replication.dict]
set routed [read_dict $dir/postroute.dict]
set attrs [dict get $before cells [lindex [lsort [dict keys [dict get $before cells]]] 0] attributes]
foreach snapshot [list $replicated $routed] {
 foreach key {signature upstream loads load_details} {
  if {[dict get $before $key] ne [dict get $snapshot $key]} {error "Reset evidence changed $key"}
 }
 set observed {}
 dict for {name data} [dict get $snapshot cells] {
  if {[dict get $data signature] ne [dict get $before signature]} {error "Reset evidence changed signature: $name"}
  if {[dict get $data attributes] ne $attrs} {error "Reset evidence changed attributes: $name"}
  lappend observed {*}[dict get $data loads] {*}[dict get $data output_ports]
 }
 if {[llength $observed] != [llength [lsort -unique $observed]]} {error "Reset evidence duplicate load ownership"}
 if {[lsort -unique $observed] ne [dict get $before loads]} {error "Reset evidence incomplete load ownership"}
 if {[dict size [dict get $snapshot cells]] <= [dict size [dict get $before cells]]} {error "Reset replication ineffective"}
}
puts "before_count\t[dict size [dict get $before cells]]"
puts "replicated_count\t[dict size [dict get $replicated cells]]"
puts "routed_count\t[dict size [dict get $routed cells]]"
foreach name [lsort [dict keys [dict get $routed cells]]] {puts "final\t$name"}
