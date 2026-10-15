#!/usr/bin/env bash
#
# Apply the M1 working-series patches (300MHz program: A1 host mmap cache,
# M1-§4.0 fanout split + canEnq fix, M0-§3.1 anchor structuring Phase A)
# to the git submodules, on top of the u280/p2/p3/nm37 patch series.
#
# Run AFTER scripts/apply-u280-patches.sh and scripts/apply-nm37-patches.sh.
# Idempotent: already-applied patches are detected and skipped.

set -euo pipefail

CYDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH_DIR="$CYDIR/fpga/patches/m1"
FPGA_SHELLS="$CYDIR/fpga/fpga-shells"
FSA_GEN="$CYDIR/generators/fsa"

apply_patch() {
    local repo="$1"
    local patch="$2"

    # FSA/easyfloat are now maintained directly in the superproject. Historical
    # patches remain for old submodule checkouts, never replay onto local sources.
    if [[ "$repo" == "$CYDIR/generators/fsa" || "$repo" == "$CYDIR/generators/easyfloat" ]] &&
       [[ -f "$CYDIR/generators/LOCAL_SOURCES.json" && ! -e "$repo/.git" ]]; then
        echo "[patches] Directly tracked source; skipping historical patch: $(basename "$patch")"
        return 0
    fi

    if [ ! -d "$repo" ]; then
        echo "ERROR: '$repo' not found. Did you run 'git submodule update --init --recursive'?" >&2
        exit 1
    fi
    if [ ! -f "$patch" ]; then
        echo "ERROR: patch file '$patch' not found." >&2
        exit 1
    fi

    if git -C "$repo" apply --check --reverse "$patch" 2>/dev/null; then
        echo "[apply-m1-patches] already applied, skipping: $(basename "$patch")"
    else
        git -C "$repo" apply "$patch"
        echo "[apply-m1-patches] applied: $(basename "$patch")"
    fi
}

echo "[apply-m1-patches] Patching generators/fsa (m1 series)..."
apply_patch "$FSA_GEN" "$PATCH_DIR/fsa-m1.patch"

echo "[apply-m1-patches] Patching fpga-shells (m1 series)..."
apply_patch "$FPGA_SHELLS" "$PATCH_DIR/fpga-shells-m1.patch"

echo "[apply-m1-patches] done."
