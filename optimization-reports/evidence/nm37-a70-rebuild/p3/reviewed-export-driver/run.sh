#!/usr/bin/env bash
set -euo pipefail
base=/home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022
out=$base/final-p3-v2
old=$base/release-drivers-p3-v2
name=chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config
[[ "$A70_OUT" == "$out" && "$A70_STAGE" == p3 && ! -e "$out/bitstream" ]]
cd "$out"
python3 "$base/reviewed-export-driver/gate.py" > "$out/logs/reviewed-export-gate.log" 2>&1
vivado -mode batch -source "$old/bitstream.tcl" -tclargs "$out/A70/fpga/$name/obj/post_route.dcp" "$out/bitstream" > "$out/logs/bitstream.log" 2>&1
python3 "$old/artifacts.py" seal --out "$out" --phase bitstream "$out/bitstream" "$out/logs/bitstream.log"
