#!/usr/bin/env bash
# Build NM37 bitstreams for the four resource-line stages (baseline / p1 / p2 / p3).
#
# Mechanism: each main-repo commit carries the era-correct apply-scripts and patch
# files; submodule pointers are identical across all four commits, so per stage we
# only checkout the main-repo commit, reset submodule working trees, re-apply the
# era patches, force re-elaboration, and run the full Vivado flow.
#
# Results land in /home/zhangsi/FSA/nm37-stage-results/<stage>/
# Logs in /home/zhangsi/FSA/nm37-stage-results/logs/

CY=/home/zhangsi/FSA
RESULTS=$CY/nm37-stage-results
LOGDIR=$RESULTS/logs
GENCFG=chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config
mkdir -p "$LOGDIR"

export RISCV=/home/zhangsi/riscv
export PATH=$HOME/circt/bin:$RISCV/bin:$PATH
source /opt/Xilinx_2020.2/Vivado/2020.2/settings64.sh

declare -A STAGE_COMMIT=(
  [baseline]=490dd747
  [p1]=ab0a602b
  [p2]=5acd5e8d
  [p3]=45d859c1
)

reset_submodules() {
  git -C "$CY/generators/fsa" checkout -- . 2>/dev/null || true
  git -C "$CY/generators/easyfloat" checkout -- . 2>/dev/null || true
  git -C "$CY/fpga/fpga-shells" checkout -- . 2>/dev/null || true
  git -C "$CY/fpga/fpga-shells" clean -fdq xilinx 2>/dev/null || true
}

echo "[$(date)] driver start" >> "$LOGDIR/overall.log"

for stage in p1 p2 p3; do
  commit=${STAGE_COMMIT[$stage]}
  log=$LOGDIR/${stage}.log
  echo "[$(date)] === stage $stage ($commit) START ===" >> "$LOGDIR/overall.log"

  (
    set -ex
    cd "$CY"
    git checkout --detach "$commit"
    git submodule status generators/fsa generators/easyfloat fpga/fpga-shells
    reset_submodules
    # p1/p2/p3: pre-apply the P0+P1 fpga-shells patch (a SUPERSET that already
    # contains the u280 migration content; verified equivalent to
    # u280+p0p1 stacked). This steers check_p1_applied() in apply-u280-patches
    # into its "skip fpga-shells-u280.patch" branch, which is the state the
    # script was designed for. NOTE: only fpga-shells is pre-patched; fsa /
    # easyfloat patches are applied by make on the clean tree as usual.
    if [ "$stage" != "baseline" ]; then
      git -C "$CY/fpga/fpga-shells" apply "$CY/fpga/patches/p0p1/fpga-shells-p0p1.patch"
    fi
    rm -rf "fpga/generated-src/$GENCFG"
    make -C fpga SUB_PROJECT=nm37 CONFIG=EmptyNM37Config bitstream
  ) >> "$log" 2>&1
  rc=$?

  dest="$RESULTS/$stage"
  mkdir -p "$dest"
  bit="$CY/fpga/generated-src/$GENCFG/obj/NM37FPGATestHarness.bit"
  if [ $rc -eq 0 ] && [ -f "$bit" ]; then
    cp "$bit" "$dest/NM37FPGATestHarness-$stage.bit"
    cp -r "$CY/fpga/generated-src/$GENCFG/obj/report" "$dest/" 2>/dev/null || true
    cp "$CY/fpga/generated-src/$GENCFG"/vivado.jou "$CY/fpga/generated-src/$GENCFG"/vivado.log "$dest/" 2>/dev/null || true
    echo "[$(date)] === stage $stage OK (bit + reports archived to $dest) ===" >> "$LOGDIR/overall.log"
  else
    echo "[$(date)] === stage $stage FAILED rc=$rc (see $log) ===" >> "$LOGDIR/overall.log"
  fi
done

echo "[$(date)] ALL DONE" >> "$LOGDIR/overall.log"
