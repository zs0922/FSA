#!/usr/bin/env bash
#
# Apply the U280 migration patches to git submodules that we do NOT want to
# modify in their upstream repos (fpga-shells, generators/fsa).
#
# The patches are stored in <repo>/fpga/patches/u280 and are applied to the
# submodule working trees with `git apply`, which never touches the submodule
# git history. Run this after `git submodule update --init --recursive`.
#
# Idempotent: already-applied patches are detected and skipped.

set -euo pipefail

CYDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH_DIR="$CYDIR/fpga/patches/u280"
FPGA_SHELLS="$CYDIR/fpga/fpga-shells"
FSA_GEN="$CYDIR/generators/fsa"

# Check if essential P1 changes are already applied
check_p1_applied() {
    # Check synth.tcl for PerformanceOptimized -retiming (core P1 change)
    if grep -q "PerformanceOptimized" "$FPGA_SHELLS/xilinx/common/tcl/synth.tcl" 2>/dev/null && \
       grep -q "\-retiming" "$FPGA_SHELLS/xilinx/common/tcl/synth.tcl" 2>/dev/null; then
        echo "[apply-u280-patches] P1 essential changes already applied, skipping fpga-shells patch"
        return 0
    fi
    return 1
}

apply_patch() {
    local repo="$1"
    local patch="$2"

    if [ ! -d "$repo" ]; then
        echo "ERROR: '$repo' not found. Did you run 'git submodule update --init --recursive'?" >&2
        exit 1
    fi
    if [ ! -f "$patch" ]; then
        echo "ERROR: patch file '$patch' not found." >&2
        exit 1
    fi

    # Check if already applied using git logic
    if git -C "$repo" apply --check --reverse "$patch" 2>/dev/null; then
        echo "[apply-u280-patches] already applied, skipping: $(basename "$patch")"
    else
        # Try applying, but don't fail if there are conflicts with essential changes
        if ! git -C "$repo" apply "$patch" 2>/dev/null; then
            echo "[apply-u280-patches] patch had conflicts, attempting three-way merge check"
            # If essential P1 changes are present, we may have conflicts that are acceptable
            if check_p1_applied; then
                echo "[apply-u280-patches] essential P1 changes present, skipping patch application"
            else
                echo "[apply-u280-patches] ERROR: patch failed and essential changes not present" >&2
                exit 1
            fi
        else
            echo "[apply-u280-patches] applied: $(basename "$patch")"
        fi
    fi
}

echo "[apply-u280-patches] Patching fpga-shells for U280..."
# Check if P1 essential changes are already applied before trying to patch
if check_p1_applied; then
    echo "[apply-u280-patches] Essential P1 changes detected in fpga-shells, skipping fpga-shells-u280.patch"
else
    apply_patch "$FPGA_SHELLS" "$PATCH_DIR/fpga-shells-u280.patch"
fi

echo "[apply-u280-patches] Patching generators/fsa for U280..."
apply_patch "$FSA_GEN" "$PATCH_DIR/fsa-u280.patch"

# P0+P1 optimization patch (seed, directives, reports, constraints)
P0P1_DIR="$CYDIR/fpga/patches/p0p1"
if [ -f "$P0P1_DIR/fpga-shells-p0p1.patch" ]; then
  # Check if P0+P1 changes already apply (report.tcl P0 collection + synth directives)
  if grep -q "PerformanceOptimized" "$FPGA_SHELLS/xilinx/common/tcl/synth.tcl" 2>/dev/null && \
     grep -q "report_power" "$FPGA_SHELLS/xilinx/common/tcl/report.tcl" 2>/dev/null; then
    echo "[apply-u280-patches] P0+P1 changes already applied (directives + reports present), skipping"
  else
    echo "[apply-u280-patches] Applying P0+P1 optimization patch..."
    apply_patch "$FPGA_SHELLS" "$P0P1_DIR/fpga-shells-p0p1.patch"
  fi
fi

# Check if HBM XSDB debug interface is disabled (UB HSDB fix, main repo)
if grep -q "USER_XSDB_INTF_EN {FALSE}" "$CYDIR/fpga/src/main/scala/u280/AXIHBM.scala" 2>/dev/null; then
    echo "[apply-u280-patches] HBM XSDB debug interface disabled (USER_XSDB_INTF_EN FALSE)"
else
    echo "[apply-u280-patches] WARNING: HBM XSDB debug interface still enabled. Fix dbg_hub/clk "
    echo "[apply-u280-patches] by setting USER_XSDB_INTF_EN FALSE in u280/AXIHBM.scala"
fi

echo "[apply-u280-patches] done."