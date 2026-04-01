#!/bin/bash
# Monitor SWE-bench runs, analyze early results, report via Telegram
# Cron: */10 * * * * /data3/drydock/scripts/monitor_swebench.sh

set -uo pipefail
export PATH="/home/bobef/miniconda3/bin:$PATH"
PYTHON="/home/bobef/miniconda3/bin/python3"
DRYDOCK="/data3/drydock"
SWEBENCH="/data3/swe_bench_runs"
LOG="$SWEBENCH/logs/monitor_swebench.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# Find the latest running log
LATEST_LOG=$(ls -t $SWEBENCH/logs/v2*.log 2>/dev/null | head -1)
if [ -z "$LATEST_LOG" ]; then
    exit 0
fi

# Count completed tasks
DONE=$(grep -c "Files match:" "$LATEST_LOG" 2>/dev/null || echo 0)
MATCHES=$(grep -c "Files match: True" "$LATEST_LOG" 2>/dev/null || echo 0)
PATCHES=$(grep "Patch:" "$LATEST_LOG" 2>/dev/null | grep -v "0L\|1L" | wc -l)
TOTAL=$(grep -c "^\[" "$LATEST_LOG" 2>/dev/null | head -1 || echo 0)

if [ "$DONE" -eq 0 ]; then
    exit 0
fi

RATE=$((MATCHES * 100 / DONE))

log "Progress: $DONE done, $MATCHES matches ($RATE%), $PATCHES patches"

# Send Telegram update every 10 tasks
LAST_REPORTED=$(cat "$SWEBENCH/logs/.last_reported" 2>/dev/null || echo 0)
if [ "$DONE" -ge $((LAST_REPORTED + 10)) ]; then
    $PYTHON "$DRYDOCK/scripts/notify_release.py" "swebench" "SWE-bench progress: $DONE tasks, $MATCHES file matches ($RATE%), $PATCHES patches. Log: $(basename $LATEST_LOG)"
    echo "$DONE" > "$SWEBENCH/logs/.last_reported"
    log "Sent Telegram update"
fi
