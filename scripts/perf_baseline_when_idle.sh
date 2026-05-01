#!/bin/bash
# perf_baseline_when_idle.sh
#
# Runs once stress harness has drained. Captures a clean (uncontaminated)
# baseline of vLLM perf via scripts/perf_sweep.py against the LIVE config.
#
# Self-disables via sentinel after one successful run. Does NOT restart
# vLLM or change config — that's the destructive sweep, held for explicit
# user trigger.
#
# Cron entry (*/15 * * * *) added at install time.

set -uo pipefail

DRYDOCK=/data3/drydock
SENTINEL="$DRYDOCK/.perf_baseline_done"
LOG="$DRYDOCK/logs/perf_baseline.log"
STRESS_LOG="/tmp/stress_2000_v10_restart_1777561483.log"
PYTHON="/home/bobef/miniconda3/bin/python3"
TELEGRAM="$DRYDOCK/scripts/notify_release.py"

mkdir -p "$DRYDOCK/logs"
exec >>"$LOG" 2>&1

echo "================ $(date -u +%FT%TZ) tick ================"

# 0. Already ran?
if [ -f "$SENTINEL" ]; then
    echo "sentinel present, exiting"
    exit 0
fi

# 1. Stress log progress check — done if final prompt logged, OR no progress
#    for 30+ minutes (harness died or finished without final marker)
if [ ! -f "$STRESS_LOG" ]; then
    echo "stress log not found, treating as idle"
else
    LAST_PROMPT=$(grep -oE "^\[([0-9]+)/1658\]" "$STRESS_LOG" | tail -1 | grep -oE "[0-9]+" | head -1)
    LAST_MOD=$(stat -c %Y "$STRESS_LOG" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    AGE=$((NOW - LAST_MOD))
    echo "stress: last prompt=$LAST_PROMPT/1658, log age=${AGE}s"

    # Done conditions:
    #   - reached 1658
    #   - no log activity for 30 min (1800s)
    if [ "${LAST_PROMPT:-0}" -lt 1658 ] && [ "$AGE" -lt 1800 ]; then
        echo "stress still active, exiting"
        exit 0
    fi
    echo "stress idle, proceeding"
fi

# 2. vLLM responsive?
if ! curl -sf http://localhost:8001/v1/models >/dev/null; then
    echo "balancer :8001 not responding, exiting"
    exit 0
fi

# 3. Run baseline — all four workloads, 5 iters each
echo "running clean baseline..."
cd "$DRYDOCK"
$PYTHON scripts/perf_sweep.py baseline --iters 5 --tag "post_stress_clean"
RC=$?
echo "perf_sweep exit=$RC"

# 4. Mark done so we don't re-run (regardless of exit code — manual rerun
#    is just `rm $SENTINEL`)
touch "$SENTINEL"

# 5. Best-effort telegram notification
if [ -x "$TELEGRAM" ] || [ -f "$TELEGRAM" ]; then
    LATEST=$(ls -1t "$DRYDOCK"/perf_results/*.json 2>/dev/null | head -1)
    SUMMARY=$($PYTHON -c "
import json, sys
try:
    d = json.load(open('$LATEST'))
    lines = ['Drydock perf baseline (post-stress, clean):']
    for s in d['summaries']:
        if s.get('ok'):
            lines.append(f\"  {s['workload']}: ttft p50={s['ttft_s_p50']:.2f}s, e2e p50={s['e2e_tok_s_p50']:.1f} tok/s\")
        else:
            lines.append(f\"  {s['workload']}: errored\")
    print('\\n'.join(lines))
except Exception as e:
    print(f'baseline ran but summary failed: {e}')
" 2>/dev/null)
    $PYTHON "$TELEGRAM" "$SUMMARY" 2>/dev/null || echo "telegram notify failed (non-fatal)"
fi

echo "done, exit $RC"
exit $RC
