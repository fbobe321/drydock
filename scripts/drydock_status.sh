#!/bin/bash
# drydock_status.sh — one-command health snapshot.
#
# Mirrors the STATUS COMMANDS block in resume.md but runs them all
# in sequence with consistent formatting. Cheap to call (<5s);
# safe to add to cron if desired.
#
# Usage:
#   bash scripts/drydock_status.sh               # full snapshot
#   bash scripts/drydock_status.sh --short       # one-screen summary
#   bash scripts/drydock_status.sh --pause-check # only sentinel state

set -uo pipefail

DRYDOCK=/data3/drydock
PY=/home/bobef/miniconda3/bin/python3
GH=/home/bobef/miniconda3/bin/gh
SHORT=0
PAUSE_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --short) SHORT=1 ;;
        --pause-check) PAUSE_ONLY=1 ;;
    esac
done

bar() { printf '%s\n' "================================================================"; }
section() { printf '\n## %s\n' "$1"; }

if [ $PAUSE_ONLY -eq 1 ]; then
    echo "pause sentinels:"
    for f in /data3/drydock/.pause_* /data3/drydock_test_projects/.pause_*; do
        [ -e "$f" ] && echo "  ACTIVE: $f"
    done | sort -u
    exit 0
fi

bar
echo "drydock status — $(date -u)"
bar

section "version + recent commits"
echo "  tag: $(git -C $DRYDOCK describe --tags --abbrev=0 2>/dev/null || echo '(none)')"
echo "  commits in last 6h:"
git -C $DRYDOCK log --oneline --since="6 hours ago" 2>/dev/null \
    | grep -vE "trip log tick" | head -8 | sed 's/^/    /'

section "production model"
PROPS=$(curl -s --max-time 5 http://localhost:8000/props 2>/dev/null)
if [ -n "$PROPS" ]; then
    echo "$PROPS" | "$PY" -c "import sys,json
try:
    d=json.load(sys.stdin)
    print(f'  model_path: {d.get(\"model_path\",\"?\")}')
    print(f'  total_slots: {d.get(\"total_slots\",\"?\")}')
except Exception as e:
    print(f'  /props parse failed: {e}')"
else
    echo "  :8000 unreachable"
fi
BAL=$(curl -s --max-time 3 http://localhost:8001/v1/models 2>/dev/null)
if [ -n "$BAL" ]; then
    echo "  balancer: OK on :8001"
else
    echo "  balancer: NOT responding on :8001"
fi

section "HLE state"
if [ -f /tmp/hle_continuous.pid ]; then
    HLE_PID=$(cat /tmp/hle_continuous.pid)
    ps -p "$HLE_PID" -o pid,etime,comm 2>/dev/null | sed 's/^/  /'
    # If the batch's run dir exists, show progress as N/limit.
    LATEST_RUN=$(ls -td "$DRYDOCK/hle_results/run_"* 2>/dev/null | head -1)
    if [ -n "$LATEST_RUN" ] && [ -f "$LATEST_RUN/results.jsonl" ]; then
        DONE=$(wc -l < "$LATEST_RUN/results.jsonl" 2>/dev/null)
        # Pull the --limit from the babysitter log line if available.
        BSLOG=$(tail -10 /tmp/hle_babysitter.log 2>/dev/null \
            | grep -oE "limit=[0-9]+" | tail -1 | cut -d= -f2)
        echo "  batch progress: ${DONE:-0} / ${BSLOG:-?}  → $LATEST_RUN"
    fi
    # Show what category this batch is on.
    LATEST_BS_LOG=$(ls -t "$DRYDOCK/logs/hle_continuous_"*.log 2>/dev/null | head -1)
    if [ -n "$LATEST_BS_LOG" ]; then
        CAT=$(grep -m1 "category filter" "$LATEST_BS_LOG" 2>/dev/null \
            | head -c 80 | sed 's/^/  /')
        [ -n "$CAT" ] && echo "$CAT"
    fi
else
    echo "  no active HLE batch (pidfile missing)"
fi
echo
"$PY" "$DRYDOCK/scripts/hle_aggregate.py" 2>/dev/null \
    | sed -n '/^=/q;p' | head -10 | sed 's/^/  /'

if [ $SHORT -eq 1 ]; then
    section "dispatch (24h)"
    "$PY" "$DRYDOCK/scripts/dispatch_report.py" --window 24h 2>/dev/null \
        | head -8 | sed 's/^/  /'
    bar
    exit 0
fi

section "dispatch (24h trailing)"
"$PY" "$DRYDOCK/scripts/dispatch_report.py" --window 24h 2>/dev/null \
    | head -20 | sed 's/^/  /'

section "curiosity queue"
"$PY" -m drydock.curiosity stats 2>/dev/null | sed 's/^/  /'

section "background loops"
echo "  stress:"
if [ -f /tmp/stress_pid.txt ]; then
    ps -p "$(cat /tmp/stress_pid.txt)" -o pid,etime,comm 2>&1 | sed 's/^/    /' | head -2
fi
echo "  autonomous_review last tick:"
tail -3 "$DRYDOCK/logs/autonomous_review.log" 2>/dev/null | sed 's/^/    /'

section "pause sentinels"
ANY=0
for f in /data3/drydock/.pause_* /data3/drydock_test_projects/.pause_*; do
    [ -e "$f" ] && echo "  ACTIVE: $f" && ANY=1
done
[ $ANY -eq 0 ] && echo "  (none — all loops live)"

section "open GitHub issues"
"$GH" issue list --repo fbobe321/drydock --state open --limit 8 2>/dev/null \
    | head -10 | sed 's/^/  /' || echo "  (gh CLI not authed)"

bar
