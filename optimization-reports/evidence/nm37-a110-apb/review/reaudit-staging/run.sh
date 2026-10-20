#!/usr/bin/env bash
# All generated files live in a fresh caller-selected directory.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
export A110_OUT=${A110_OUT:?Set A110_OUT to a fresh absolute output directory}
[[ "$A110_OUT" = /* && "$A110_OUT" != *' '* && "$ROOT" != *' '* ]] || { echo 'Absolute paths without spaces required' >&2; exit 2; }
export USE_CHISEL6=1
# Chisel's resolver otherwise downloads its own firtool version, independent
# of the binary used by the board Makefile and recorded in environment.json.
if command -v firtool >/dev/null 2>&1; then
  export CHISEL_FIRTOOL_PATH=$(dirname "$(command -v firtool)")
fi
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Xmx16G -Xss8M -XX:ActiveProcessorCount=16 -Dfsa.pePipeline=true -Dfsa.fanoutSplit=false -Dfsa.pipelineConservative=false -Dfsa.nm37MHz=110"
helper="$ROOT/scripts/nm37-a110"
jar="$A110_OUT/generator.jar"
name=chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config
build="$A110_OUT/A110/fpga/$name"
phase=${1:-help}
case "$phase" in
  help) echo 'Phases: check prepare units formal hbm-startup sim elaborate implement physical bitstream reaudit reaudit-accept reaudit-verify bitstream-from-reaudit'; exit 0;;
  check) python3 "$helper/check.py" sources; exit;;
  physical)
    python3 "$helper/check.py" gate
    python3 "$helper/check.py" physical
    ;;
  reaudit)
    python3 "$helper/reaudit.py" run --source "$ROOT" --build "$A110_OUT" \
      --out "${A110_REAUDIT_OUT:?Set a new A110_REAUDIT_OUT outside the original build/source}"
    ;;
  reaudit-accept)
    python3 "$helper/reaudit.py" accept --out "${A110_REAUDIT_OUT:?Set A110_REAUDIT_OUT}" \
      --attempt "${A110_REAUDIT_ATTEMPT:?Set a new acceptance attempt name}"
    ;;
  reaudit-verify)
    python3 "$helper/reaudit.py" verify --out "${A110_REAUDIT_OUT:?Set A110_REAUDIT_OUT}" \
      --acceptance "${A110_REAUDIT_PASS:?Set the exact revised acceptance path}"
    ;;
  bitstream-from-reaudit)
    python3 "$helper/reaudit.py" bitstream-from-reaudit \
      --out "${A110_REAUDIT_OUT:?Set A110_REAUDIT_OUT}" \
      --acceptance "${A110_REAUDIT_PASS:?Set the exact revised acceptance path}" \
      --destination "${A110_BITSTREAM_OUT:?Set a new bitstream export directory}"
    ;;
  prepare)
    [[ ! -e "$A110_OUT" ]] || { echo 'Refusing to reuse output directory' >&2; exit 2; }
    [[ ! -d "$ROOT/nm37-m1-pipeline" ]] || { echo "Use a fresh checkout; this workspace contains frozen experiment artifacts" >&2; exit 2; }
    python3 "$helper/check.py" sources
    mkdir -p "$A110_OUT/logs"
    # shell inputs are snapshotted from the pinned submodule plus exact patch.
    python3 - "$ROOT" "$A110_OUT" <<'PYSHELL'
import json, shutil, sys
from pathlib import Path
root, out = map(Path, sys.argv[1:])
lock = json.loads((root / 'fpga/baselines/nm37-a110/source-lock.json').read_text())
for name in lock['shell_hashes']:
    target = out / 'shell' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / 'fpga/fpga-shells' / name, target)
PYSHELL
    for tool in java firtool verilator vivado; do command -v "$tool"; done
    { java -version; firtool --version; verilator --version; vivado -version; } > "$A110_OUT/logs/tools.txt" 2>&1
    python3 - "$A110_OUT/logs/tools.txt" <<'PYTOOLS'
import sys
from pathlib import Path
versions = Path(sys.argv[1]).read_text()
for expected in ['2020.2_AR75986', '20.0.2', 'firtool-1.75.0', 'Verilator 5.022']:
    if expected not in versions:
        raise SystemExit('Baseline tool version missing: ' + expected)
PYTOOLS
    python3 "$helper/environment.py"
    # Use a stable disposable assembly target and copy out: sbt may delete its
    # previous output when assemblyOutputPath changes. Use a fresh checkout.
    make -C "$ROOT/fpga" SUB_PROJECT=nm37 CONFIG=EmptyNM37Config \
      GENERATOR_CLASSPATH="$ROOT/.classpath_cache/nm37-a110-assembly.jar" \
      "$ROOT/.classpath_cache/nm37-a110-assembly.jar" > "$A110_OUT/logs/assembly.log" 2>&1
    cp "$ROOT/.classpath_cache/nm37-a110-assembly.jar" "$jar"
    python3 "$helper/check.py" record
    ;;
  units)
    python3 "$helper/check.py" run
    [[ ! -e "$A110_OUT/logs/unit.log" ]] || { echo 'Unit log already exists'; exit 2; }
    cd "$ROOT"
    java -jar scripts/sbt-launch.jar -Dsbt.supershell=false \
      "fsa/Test/runMain fsa.PipelineVerification" \
      "fsa/Test/runMain fsa.PipelineVerification emit $A110_OUT/formal" \
      "fsa/Test/runMain fsa.PipelinedScheduleVerification $A110_OUT/score-events.json" \
      "fsa/testOnly fsa.ControlSignalTest -- -z row -z overlapped -z consecutive" \
      > "$A110_OUT/logs/unit.log" 2>&1
    python3 "$helper/check.py" seal units
    ;;
  formal)
    python3 "$helper/check.py" run
    [[ ! -e "$A110_OUT/formal/results.json" ]] || { echo 'Formal results already exist'; exit 2; }
    python3 "$helper/verify_equivalence.py"
    python3 "$helper/check.py" seal formal
    ;;
  hbm-startup)
    python3 "$helper/check.py" run
    [[ ! -e "$A110_OUT/hbm-startup" ]] || { echo 'HBM startup output already exists'; exit 2; }
    mkdir -p "$A110_OUT/hbm-startup"
    cd "$ROOT"
    java -jar scripts/sbt-launch.jar -Dsbt.supershell=false \
      "chipyard_fpga/Test/runMain chipyard.fpga.u280.HBMStartupVerification $A110_OUT/hbm-startup/rtl" \
      > "$A110_OUT/hbm-startup/emission.log" 2>&1
    python3 "$helper/verify_hbm_startup.py" --rtl "$A110_OUT/hbm-startup/rtl" --out "$A110_OUT/hbm-startup"
    python3 "$helper/check.py" seal hbm-startup
    ;;
  sim)
    python3 "$helper/check.py" run
    size=${2:?Expected array size 4, 8, or 16}
    case "$size" in 4|8|16) ;; *) exit 2;; esac
    out="$A110_OUT/sim$size"
    [[ ! -e "$out" ]] || { echo 'Simulation output already exists'; exit 2; }
    mkdir -p "$out"
    cfg="AXI4FSA${size}X${size}Fp16Config"
    make -C "$ROOT/sims/verilator" -j"${A110_JOBS:-8}" CONFIG="$cfg" \
      CXX="${CXX:-g++}" CC="${CC:-gcc}" LINK="${CXX:-g++}" \
      GENERATOR_CLASSPATH="$jar" -o "$jar" \
      build_dir="$out/generated-src/chipyard.harness.TestHarness.$cfg" \
      sim="$out/simulator" default > "$out/build.log" 2>&1
    for seq in "$size" "$((size * 2))"; do
      mkdir "$out/seq$seq"
      "${FSA_PYTHON:-$ROOT/generators/fsa/python/.venv/bin/python}" "$helper/verify_sim.py" \
        --binary "$out/simulator" --seq "$seq" --out "$out/seq$seq" \
        --config "$out/generated-src/chipyard.harness.TestHarness.$cfg/chipyard.harness.TestHarness.$cfg.FSAConfig.json" \
        > "$out/seq$seq/test.log" 2>&1
    done
    python3 "$helper/check.py" seal "sim$size"
    ;;
  elaborate)
    python3 "$helper/check.py" run
    [[ ! -e "$A110_OUT/A110" ]] || { echo 'Elaboration already exists'; exit 2; }
    mkdir -p "$A110_OUT/A110"
    make -C "$ROOT/fpga" SUB_PROJECT=nm37 CONFIG=EmptyNM37Config \
      GENERATOR_CLASSPATH="$jar" -o "$jar" build_dir="$build" \
      "$build/$name.vsrcs.f" > "$A110_OUT/A110/elaboration.log" 2>&1
    python3 "$helper/check.py" seal elaborate
    ;;
  implement)
    python3 "$helper/check.py" gate
    [[ ! -e "$build/obj" ]] || { echo 'Implementation already exists'; exit 2; }
    cd "$build"
    mapfile -t ip_scripts < <(find "$build" -name '*.vivado.tcl' | sort)
    vivado -mode batch -source "$helper/implement.tcl" \
      -tclargs -top-module NM37FPGATestHarness -F "$build/$name.vsrcs.f" \
      -board nm37 -ip-vivado-tcls "${ip_scripts[*]}" > "$A110_OUT/A110/implementation.log" 2>&1
    python3 "$helper/check.py" physical
    ;;
  bitstream)
    python3 "$helper/check.py" gate
    python3 "$helper/check.py" physical
    vivado -mode batch -source "$ROOT/fpga/scripts/m1_a110_bitstream.tcl" \
      -tclargs "$build/obj/post_route.dcp" "$A110_OUT/bitstream" > "$A110_OUT/logs/bitstream.log" 2>&1
    ;;
  *) echo "Unknown phase: $phase" >&2; exit 2;;
esac
