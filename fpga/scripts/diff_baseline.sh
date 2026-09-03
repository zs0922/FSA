#!/bin/bash
# diff_baseline.sh - Compare two build baseline directories
# Usage: ./diff_baseline.sh <old_report_dir> <new_report_dir>
# Example: ./diff_baseline.sh build_old/report build_new/report

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <old_report_dir> <new_report_dir>"
  exit 1
fi

OLD="$1"
NEW="$2"

echo "=========================================="
echo " Baseline Diff: $OLD vs $NEW"
echo "=========================================="

# --- Timing ---
echo ""
echo "--- TIMING ---"
if [ -f "$OLD/timing.txt" ] && [ -f "$NEW/timing.txt" ]; then
  echo "WNS (old):"
  grep -A2 "Design Timing Summary" "$OLD/timing.txt" | grep "WNS" || true
  echo "WNS (new):"
  grep -A2 "Design Timing Summary" "$NEW/timing.txt" | grep "WNS" || true
  echo ""
  echo "TNS (old):"
  grep "TNS" "$OLD/timing.txt" | head -1 || true
  echo "TNS (new):"
  grep "TNS" "$NEW/timing.txt" | head -1 || true
else
  echo "timing.txt not found in one or both directories"
fi

# --- Utilization ---
echo ""
echo "--- UTILIZATION ---"
for res in CLB LUT FF BRAM DSP URAM; do
  old_val=$(grep -oP "${res}.*?(\d+)" "$OLD/utilization.txt" 2>/dev/null | tail -1 | grep -oP "\d+$" || echo "N/A")
  new_val=$(grep -oP "${res}.*?(\d+)" "$NEW/utilization.txt" 2>/dev/null | tail -1 | grep -oP "\d+$" || echo "N/A")
  if [ "$old_val" != "N/A" ] || [ "$new_val" != "N/A" ]; then
    echo "$res: $old_val -> $new_val"
  fi
done

# --- Power ---
echo ""
echo "--- POWER ---"
if [ -f "$OLD/power.txt" ] && [ -f "$NEW/power.txt" ]; then
  echo "Total power (old):"
  grep -i "Total On-Chip" "$OLD/power.txt" | head -1 || true
  echo "Total power (new):"
  grep -i "Total On-Chip" "$NEW/power.txt" | head -1 || true
else
  echo "power.txt not found in one or both directories"
fi

# --- DRC ---
echo ""
echo "--- DRC ---"
if [ -f "$OLD/drc.txt" ] && [ -f "$NEW/drc.txt" ]; then
  echo "DRC violations (old):"
  grep -c "CRITICAL\|ERROR\|WARNING" "$OLD/drc.txt" 2>/dev/null || echo "0"
  echo "DRC violations (new):"
  grep -c "CRITICAL\|ERROR\|WARNING" "$NEW/drc.txt" 2>/dev/null || echo "0"
else
  echo "drc.txt not found in one or both directories"
fi

# --- Fanout ---
echo ""
echo "--- HIGH FANOUT (top 5) ---"
if [ -f "$OLD/fanout.txt" ] && [ -f "$NEW/fanout.txt" ]; then
  echo "Old top fanout:"
  grep -A1 "Fanout" "$OLD/fanout.txt" | head -10 || true
  echo "---"
  echo "New top fanout:"
  grep -A1 "Fanout" "$NEW/fanout.txt" | head -10 || true
else
  echo "fanout.txt not found in one or both directories"
fi

# --- Congestion ---
echo ""
echo "--- CONGESTION ---"
if [ -f "$OLD/congestion.txt" ] && [ -f "$NEW/congestion.txt" ]; then
  diff --brief "$OLD/congestion.txt" "$NEW/congestion.txt" 2>/dev/null && echo "Congestion reports identical" || echo "Congestion reports differ (see full diff)"
  diff "$OLD/congestion.txt" "$NEW/congestion.txt" 2>/dev/null | head -30 || true
else
  echo "congestion.txt not found in one or both directories"
fi

# --- SLR Utilization ---
echo ""
echo "--- SLR UTILIZATION ---"
if [ -f "$OLD/utilization_slr.txt" ] && [ -f "$NEW/utilization_slr.txt" ]; then
  diff --brief "$OLD/utilization_slr.txt" "$NEW/utilization_slr.txt" 2>/dev/null && echo "SLR utilization reports identical" || echo "SLR utilization reports differ"
  diff "$OLD/utilization_slr.txt" "$NEW/utilization_slr.txt" 2>/dev/null | head -30 || true
else
  echo "utilization_slr.txt not found in one or both directories"
fi

echo ""
echo "=========================================="
echo " Diff complete."
echo "=========================================="
