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

    if git -C "$repo" apply --check --reverse "$patch" 2>/dev/null; then
        echo "[apply-u280-patches] already applied, skipping: $(basename "$patch")"
    else
        git -C "$repo" apply "$patch"
        echo "[apply-u280-patches] applied: $(basename "$patch")"
    fi
}

echo "[apply-u280-patches] Patching fpga-shells for U280..."
apply_patch "$FPGA_SHELLS" "$PATCH_DIR/fpga-shells-u280.patch"

echo "[apply-u280-patches] Patching generators/fsa for U280..."
apply_patch "$FSA_GEN" "$PATCH_DIR/fsa-u280.patch"

echo "[apply-u280-patches] done."
