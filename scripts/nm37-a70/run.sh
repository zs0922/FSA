#!/usr/bin/env bash
# Run against an independent historical checkout prepared by install_historical.py.
set -euo pipefail
helper=$(cd "$(dirname "$0")" && pwd)
src=${A70_SOURCE:?Set A70_SOURCE to the historical checkout}
out=${A70_OUT:?Set A70_OUT to a new absolute build directory}
stage=${A70_STAGE:?Set A70_STAGE to p2 or p3}
case "$stage" in p2|p3) ;; *) exit 2;; esac
[[ "$src" = /* && "$out" = /* && "$src$out" != *' '* ]] || { echo 'Absolute paths without spaces required'; exit 2; }
export USE_CHISEL6=1
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Xmx16G -Xss8M -XX:ActiveProcessorCount=16 -Dsbt.supershell=false -Dsbt.server.autostart=false"
phase=${1:?prepare assembly units formal sim elaborate implement bitstream}
name=chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config
build=$out/A70/fpga/$name
jar=$out/generator.jar
check() { python3 "$helper/artifacts.py" check --out "$out"; }
seal() { python3 "$helper/artifacts.py" seal --out "$out" --phase "$1" "${@:2}"; }
python3 "$src/scripts/nm37-a70-prepare.py" --root "$src" --stage "$stage"
if [[ "$phase" == prepare ]]; then
  [[ ! -e "$out" ]] || { echo 'Use a new build output directory'; exit 2; }
  mkdir -p "$out/logs"
  python3 "$helper/source_lock.py" capture --source "$src" --lock "$out/source-lock.json"
  python3 - "$src" "$out" "$stage" <<'PY'
import hashlib,json,shutil,subprocess,sys
from pathlib import Path
src,out=map(Path,sys.argv[1:3]);stage=sys.argv[3]
lock=json.loads((out/'source-lock.json').read_text())
for name in lock['files']:
    if name.startswith('fpga/fpga-shells/'):
        p=src/name
        if p.is_file():
            dest=out/'shell'/p.relative_to(src/'fpga/fpga-shells')
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
(out/'build.json').write_text(json.dumps(dict(stage=stage,source=str(src),root_commit=lock['root_commit'],source_lock_sha256=hashlib.sha256((out/'source-lock.json').read_bytes()).hexdigest()),indent=2)+'\n')
PY
  { java -version; firtool --version; verilator --version; vivado -version; } > "$out/logs/tools.txt" 2>&1
  python3 "$helper/artifacts.py" record --out "$out"
  exit
fi
check
cd "$src"
case "$phase" in
  assembly)
    [[ ! -e "$jar" && ! -e "$out/logs/assembly.log" ]]
    make -C fpga SUB_PROJECT=nm37 CONFIG=EmptyNM37Config GENERATOR_CLASSPATH="$src/.classpath_cache/a70-release.jar" "$src/.classpath_cache/a70-release.jar" > "$out/logs/assembly.log" 2>&1
    cp "$src/.classpath_cache/a70-release.jar" "$jar"
    seal assembly "$jar" "$out/logs/assembly.log"
    ;;
  units)
    [[ "$stage" == p3 && ! -e "$out/logs/units.log" ]]
    java -jar scripts/sbt-launch.jar -Dsbt.supershell=false \
      'set fsa / Test / sources := (fsa / Test / sources).value.filter(f => Set("A70AccumulatorVerification.scala", "A70LegacyExp2.scala", "A70LegacyReciprocal.scala", "A70LegacyFMA.scala").contains(f.getName))' \
      'fsa/Test/runMain fsa.A70AccumulatorVerification' \
      "fsa/Test/runMain fsa.A70AccumulatorVerification emit $out/formal-rtl" > "$out/logs/units.log" 2>&1
    seal units "$out/logs/units.log" "$out/formal-rtl"
    ;;
  formal)
    [[ "$stage" == p3 && ! -e "$out/formal" ]]
    python3 "$helper/verify/window_proof/run-proof.py" --rtl "$out/formal-rtl" --out "$out/formal" > "$out/logs/formal.log" 2>&1
    seal formal "$out/formal" "$out/logs/formal.log"
    ;;
  sim)
    size=${2:?4 or 16}
    case "$size" in 4) cfg=AXI4FSA4X4Fp16Config;; 16) cfg=A70SinglePort16X16Fp16Config;; *) exit 2;; esac
    simout=$out/sim$size
    [[ ! -e "$simout" ]]
    mkdir "$simout"
    make -C sims/verilator -j"${A70_JOBS:-16}" CXX="${CXX:-g++}" CC="${CC:-gcc}" LINK="${CXX:-g++}" CONFIG="$cfg" \
      GENERATOR_CLASSPATH="$jar" -o "$jar" build_dir="$simout/build" sim="$simout/simulator" default > "$simout/build.log" 2>&1
    for seq in "$size" "$((size * 2))"; do
      PYTHONPATH="$src/generators/easyfloat:$src/generators/fsa/python${PYTHONPATH:+:$PYTHONPATH}" \
      "${FSA_PYTHON:?Set FSA_PYTHON to the prepared Python interpreter}" "$helper/verify/verify_sim.py" \
        --source "$src" --binary "$simout/simulator" --config "$simout/build/chipyard.harness.TestHarness.$cfg.FSAConfig.json" \
        --out "$simout/seq$seq" --seq "$seq" > "$simout/seq$seq.log" 2>&1
    done
    seal "sim$size" "$simout"
    ;;
  elaborate)
    [[ ! -e "$out/A70" ]]
    make -C fpga SUB_PROJECT=nm37 CONFIG=EmptyNM37Config GENERATOR_CLASSPATH="$jar" -o "$jar" build_dir="$build" "$build/$name.vsrcs.f" > "$out/logs/elaboration.log" 2>&1
    seal elaborate "$build" "$out/logs/elaboration.log"
    ;;
  implement)
    python3 "$helper/artifacts.py" gate --out "$out"
    [[ ! -e "$build/obj" && ! -e "$out/logs/implementation.log" ]]
    cd "$build"
    mapfile -t ip_scripts < <(find "$build" -name '*.vivado.tcl' | sort)
    vivado -mode batch -source "$helper/implement.tcl" -tclargs -top-module NM37FPGATestHarness \
      -F "$build/$name.vsrcs.f" -board nm37 -ip-vivado-tcls "${ip_scripts[*]}" > "$out/logs/implementation.log" 2>&1
    python3 "$helper/physical.py" --obj "$build/obj" --out "$out/physical.passed.json"
    seal implement "$out/physical.passed.json" "$build/obj/post_route.dcp" "$build/obj/report" "$build/obj/audit"
    ;;
  bitstream)
    python3 "$helper/artifacts.py" gate --out "$out" --phase bitstream
    python3 "$helper/physical.py" --obj "$build/obj" --out "$out/physical-export-recheck.json"
    vivado -mode batch -source "$helper/bitstream.tcl" -tclargs "$build/obj/post_route.dcp" "$out/bitstream" > "$out/logs/bitstream.log" 2>&1
    seal bitstream "$out/bitstream" "$out/logs/bitstream.log"
    ;;
  *) echo "Unknown phase: $phase"; exit 2;;
esac
