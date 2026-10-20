# Read-only reset distribution audit. Source this file after opening a design.
namespace eval reset_audit {
 variable stem {}
 # ClockGroup membership can renumber ResetWrangler catchers. Resolve the
 # DUT chain by its actual clock, never by its elaboration-order suffix.
 proc select_dut {} {
  variable stem
  set dut [get_clocks -quiet clk_out1_harnessSysPLL]
  if {[llength $dut] != 1 || abs([get_property PERIOD $dut] - 1000.0/110.0) > 0.001} {error "Reset audit: missing 110 MHz DUT clock"}
  set matches {}
  foreach c [get_cells -quiet -hier -filter {NAME =~ wrangler/*/io_sync_reset_chain/output_chain/sync_2_reg && REF_NAME == FDCE}] {
   if {[get_clocks -quiet -of_objects [pin $c C]] eq $dut} {
    lappend matches [file dirname $c]
   }
  }
  if {[llength $matches] != 1} {error "Reset audit: expected one clock-identified DUT reset chain, found $matches"}
  set selected [lindex $matches 0]
  if {$stem ne {} && $stem ne $selected} {error "Reset audit: DUT reset identity changed: $stem -> $selected"}
  set stem $selected
  return $stem
 }
 proc pin {c role} {
  set p [get_pins -quiet -of_objects $c -filter "REF_PIN_NAME == $role"]
  if {[llength $p] != 1} {error "Reset audit: expected exactly one $role pin on $c"}
  return $p
 }
 proc finals {} {
  variable stem
  select_dut
  set original [get_cells -quiet $stem/sync_0_reg]
  if {[llength $original] != 1 || [get_property REF_NAME $original] ne "FDCE"} {error "Reset audit: canonical sync_0_reg missing/changed; review new netlist naming"}
  set fs [lsort [get_cells -quiet -hier -filter "NAME =~ ${stem}/sync_0* && REF_NAME == FDCE"]]
  set prev [get_cells -quiet $stem/sync_1_reg]
  if {[llength $prev] != 1} {error "Reset audit: canonical sync_1_reg missing"}
  set nets [get_nets -quiet -segments -of_objects [pin $prev Q]]
  set loads [get_pins -quiet -leaf -of_objects $nets -filter {DIRECTION == IN}]
  set actual {}
  foreach p $loads {
   set c [get_cells -of_objects $p]
   if {[get_property REF_NAME $c] ne "FDCE" || [get_property REF_PIN_NAME $p] ne "D"} {error "Reset audit: unexpected sync_1 output load $p"}
   lappend actual $c
  }
  if {[lsort -unique $actual] ne $fs} {error "Reset audit: named finals differ from actual sync_1 loads; possible renamed/unrecognized replica"}
  return $fs
 }
 proc drivers {pin} {
  set nets [get_nets -quiet -segments -of_objects $pin]
  set out [get_pins -quiet -leaf -of_objects $nets -filter {DIRECTION == OUT}]
  set ports [get_ports -quiet -of_objects $nets -filter {DIRECTION == IN}]
  return [lsort -unique [concat $out $ports]]
 }
 proc attributes {c} {
  set result {}
  foreach prop {ASYNC_REG DONT_TOUCH} {
   if {[lsearch -exact [list_property $c] $prop] >= 0} {dict set result $prop [list present [get_property $prop $c]]} else {dict set result $prop absent}
  }
  return $result
 }
 proc signature {c} {
  set result [list [get_property REF_NAME $c] [get_property INIT $c]]
  foreach p {D C CE CLR} {
   set d [drivers [pin $c $p]]
   if {[llength $d] != 1} {error "Reset audit: $c/$p ambiguous drivers: $d"}
   lappend result $p $d
  }
  foreach prop {IS_C_INVERTED IS_CLR_INVERTED IS_D_INVERTED} {
   if {[lsearch -exact [list_property $c] $prop] >= 0} {lappend result $prop [list present [get_property $prop $c]]} else {lappend result $prop absent}
  }
  return $result
 }
 proc snapshot {path} {
  variable stem
  set finals [finals]
  if {![llength $finals]} {error "Reset audit: no final-stage FDCE"}
  set state [dict create]
  foreach name {sync_1_reg sync_2_reg} {
   set c [get_cells -quiet $stem/$name]
   if {[llength $c] != 1 || [get_property REF_NAME $c] ne "FDCE"} {error "Reset audit: upstream $name missing or changed primitive"}
   dict set state upstream $name [dict create signature [signature $c] attributes [attributes $c]]
  }
  set all_loads {}
  set load_details {}
  set fabric_nets {}
  set reference {}
  foreach c [lsort $finals] {
   set signature [signature $c]
   if {$reference eq {}} {set reference $signature}
   if {$signature ne $reference} {error "Reset audit: non-equivalent final FDCE $c: $signature != $reference"}
   set q [pin $c Q]
   if {[drivers $q] ne [list $q]} {error "Reset audit: Q net not uniquely driven by $q"}
   set nets [get_nets -quiet -segments -of_objects $q]
   set loads [lsort -unique [get_pins -quiet -leaf -of_objects $nets -filter {DIRECTION == IN}]]
   set inouts [get_ports -quiet -of_objects $nets -filter {DIRECTION == INOUT}]
   if {[llength $inouts]} {error "Reset audit: unhandled bidirectional port on $q: $inouts"}
   set output_ports [lsort -unique [get_ports -quiet -of_objects $nets -filter {DIRECTION == OUT}]]
   if {![llength $loads] && ![llength $output_ports]} {error "Reset audit: unloaded final-stage $c"}
   foreach p $output_ports {dict set load_details $p [list output_port [get_property DIRECTION $p]]}
   lappend all_loads {*}$output_ports
   set slrs [dict create]
   set fabric 0
   foreach p $loads {
    set lc [get_cells -of_objects $p]
    dict set load_details $p [list pin [get_property REF_NAME $lc] [get_property REF_PIN_NAME $p]]
    set slr [get_property SLR_INDEX $lc]
    dict incr slrs $slr
    if {![string match BUFG* [get_property REF_NAME $lc]]} {incr fabric}
   }
   if {$fabric > 1} {lappend fabric_nets {*}[get_nets -quiet -of_objects [pin $c Q]]}
   lappend all_loads {*}$loads
   dict set state cells $c [dict create signature $signature attributes [attributes $c] output_ports $output_ports location [get_property LOC $c] slr [get_property SLR_INDEX $c] loads $loads fanout [llength $loads] load_slrs $slrs]
  }
  if {[llength $all_loads] != [llength [lsort -unique $all_loads]]} {error "Reset audit: duplicate sink ownership"}
  set canonical_details {}
  foreach p [lsort [dict keys $load_details]] {dict set canonical_details $p [dict get $load_details $p]}
  dict set state load_details $canonical_details
  dict set state signature $reference
  dict set state loads [lsort -unique $all_loads]
  dict set state fabric_nets [lsort -unique $fabric_nets]
  set f [open $path w]; puts $f $state; close $f
  return $state
 }
 proc compare {before after path} {
  set f [open $path w]
  foreach key {signature upstream loads load_details} {
   set a [dict get $before $key]; set b [dict get $after $key]
   puts $f "$key before=$a\n$key after=$b"
   if {$a ne $b} {puts $f "FAIL: $key changed"; close $f; error "Reset structural audit failed: $key changed; inspect $path"}
  }
  set baseline_attributes [dict get $before cells [lindex [lsort [dict keys [dict get $before cells]]] 0] attributes]
  dict for {c data} [dict get $after cells] {
   if {[dict get $data attributes] ne $baseline_attributes} {puts $f "FAIL: final attributes changed at $c"; close $f; error "Reset audit: final attributes changed at $c"}
  }
  puts $f "PASS: identical FDCE signatures, exact direct driver-pin identities, and sink ownership union; no added sequential stage."
  close $f
 }
 proc coverage {dir {strict 1}} {
  variable stem
  file mkdir $dir
  set f [open $dir/endpoint_coverage.txt w]
  set failures {}
  set finals [finals]
  if {![llength $finals]} {close $f; error "No finals for timing coverage"}
  foreach c [lsort $finals] {
   foreach pin {D CLR} {
    foreach kind {max min} {
     set paths [get_timing_paths -quiet -to [pin $c $pin] -delay_type $kind -max_paths 1 -nworst 1]
     if {![llength $paths]} {
      puts $f "FAIL $c/$pin $kind uncovered"
      lappend failures "$c/$pin $kind uncovered"
     } else {
      set slack [get_property SLACK [lindex $paths 0]]
      puts $f "$c/$pin $kind slack=$slack"
      if {![string is double -strict $slack] || $slack < 0} {lappend failures "$c/$pin $kind slack=$slack"}
     }
    }
   }
  }
  puts $f "failures=$failures"
  close $f
  if {[llength $failures] && $strict} {error "Reset endpoint timing coverage/slack failed; see $dir/endpoint_coverage.txt"}
  return $failures
 }
 proc timing {dir {strict 1}} {
  variable stem
  file mkdir $dir
  set finals [finals]
  set ds [get_pins -of_objects $finals -filter {REF_PIN_NAME == D}]
  set qs [get_pins -of_objects $finals -filter {REF_PIN_NAME == Q}]
  set clrs [get_pins -of_objects $finals -filter {REF_PIN_NAME == CLR}]
  foreach kind {max min} {
   report_timing -to $ds -delay_type $kind -max_paths 100 -nworst 1 -input_pins -file $dir/upstream_$kind.rpt
   report_timing -from $qs -delay_type $kind -max_paths 100 -nworst 1 -input_pins -file $dir/distribution_$kind.rpt
   report_timing -to $clrs -delay_type $kind -max_paths 100 -nworst 1 -input_pins -file $dir/async_release_$kind.rpt
  }
  # Empty CLR timing is not a pass: asynchronous source or false-path constraints
  # may exclude recovery/removal; preserve exceptions and check_timing for review.
  report_exceptions -file $dir/exceptions.rpt
  check_timing -verbose -file $dir/check_timing.rpt
  coverage $dir $strict
 }
}
