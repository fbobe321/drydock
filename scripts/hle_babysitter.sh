#!/bin/bash
# hle_babysitter.sh — keep an HLE eval batch running on the hour.
#
# Cron line:
#   0 * * * * /data3/drydock/scripts/hle_babysitter.sh
#
# Responsibilities (in order, fail-closed on any blocker):
#  - If $PAUSE_FILE exists, log and exit. Lets the operator stop the
#    loop without removing the cron entry.
#  - If a previous HLE eval kicked off by this script is still alive,
#    log and exit. Avoids stacking concurrent evals fighting for the
#    single llama.cpp slot.
#  - If the stress harness is mid-batch, skip this tick. Stress already
#    saturates inference; competing with it just degrades both signals.
#  - If autonomous_review is mid-run, skip — it owns the model briefly.
#  - If the production balancer at :8001 isn't returning /v1/models,
#    skip — there's no point firing requests at a broken backend.
#  - Otherwise, kick off a 10-Q math HLE batch, record the PID, and
#    log a single status line.
#
# Each batch writes results into the standard
# /data3/drydock/hle_results/run_<ts>/ tree. Long-running summary work
# (multi-batch aggregation, per-pattern grouping) is a separate job —
# this script just keeps the wheel turning.
#
# Files:
#   $DRYDOCK/scripts/hle_babysitter.sh         — this script
#   $DRYDOCK/.pause_hle_babysitter             — pause sentinel (touch to stop)
#   /tmp/hle_continuous.pid                    — current batch PID (this script)
#   /tmp/hle_babysitter.log                    — this script's append-only log
#   $DRYDOCK/logs/hle_continuous_<UTC>.log     — per-batch eval stdout
#
# Manual control:
#   touch /data3/drydock/.pause_hle_babysitter      # pause
#   rm    /data3/drydock/.pause_hle_babysitter      # resume
#   kill $(cat /tmp/hle_continuous.pid)             # stop current batch

set -u

DRYDOCK=/data3/drydock
LOG=/tmp/hle_babysitter.log
PID_FILE=/tmp/hle_continuous.pid
PAUSE_FILE="$DRYDOCK/.pause_hle_babysitter"
PY=/home/bobef/miniconda3/bin/python3

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

if [ -f "$PAUSE_FILE" ]; then
    log "PAUSED via $PAUSE_FILE"
    exit 0
fi

# Already running? PID file written by this script, so we know we kicked it.
# Kill the prior batch if it's been alive past STALE_AFTER_SEC (default 2h).
# Each Q is ≤481s; a 10-Q batch should never exceed ~5400s. Anything past
# 2h is wedged (model backend hang, infinite retry, etc.) — kill it so the
# next cron tick can start fresh instead of waiting on a zombie.
STALE_AFTER_SEC=${HLE_BABYSITTER_STALE_AFTER_SEC:-7200}
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    PID=$(cat "$PID_FILE")
    AGE_SEC=$(($(date +%s) - $(stat -c %Y /proc/"$PID"/stat 2>/dev/null || echo 0)))
    if [ "$AGE_SEC" -gt "$STALE_AFTER_SEC" ]; then
        log "killing stale batch (pid=$PID, age=${AGE_SEC}s > ${STALE_AFTER_SEC}s)"
        kill "$PID" 2>/dev/null
        sleep 2
        kill -9 "$PID" 2>/dev/null
        # Fall through to spawn a fresh batch below.
    else
        log "skip — prior HLE eval still alive (pid $PID, age ${AGE_SEC}s)"
        exit 0
    fi
fi

# Stress harness owns the inference slot during its runs.
# Use the PID file instead of pgrep — pgrep matches claude processes whose
# --append-system-prompt text happens to contain "stress_2000", causing
# false-positive skips every time autonomous_review runs.
STRESS_PID_FILE=/tmp/stress_pid.txt
if [ -f "$STRESS_PID_FILE" ] && kill -0 "$(cat "$STRESS_PID_FILE")" 2>/dev/null; then
    log "skip — stress run in progress (pid $(cat "$STRESS_PID_FILE"))"
    exit 0
fi

# autonomous_review uses the model briefly each tick.
if pgrep -fa "autonomous_review\.sh\b" >/dev/null 2>&1; then
    log "skip — autonomous_review mid-run"
    exit 0
fi

# Balancer health check — bail if the production backend isn't talking.
if ! curl -s --max-time 5 http://localhost:8001/v1/models | grep -q '"id":"gemma4"'; then
    log "skip — balancer not healthy at :8001"
    exit 0
fi

# Default batch size: 10 questions per hour. At Q3_K_M timings this
# fits in ~80 minutes worst case (10 × 481s), so the next cron tick
# either finds the previous one done OR skips because still alive.
#
# Category rotation: HLE is bigger than just Math. Rotate by UTC hour
# across the 8 HLE categories so a 24h window touches each one ~3×
# instead of stockpiling 100% Math attempts. Operator can pin a
# category by setting HLE_BABYSITTER_CATEGORY explicitly, or set it
# to '' for no filter (whole exam).
LIMIT=${HLE_BABYSITTER_LIMIT:-10}

if [ "${HLE_BABYSITTER_CATEGORY+set}" = "set" ]; then
    CATEGORY=${HLE_BABYSITTER_CATEGORY}
else
    # 8 buckets keyed off the UTC hour-of-day. Math gets prioritised
    # — it's the largest HLE category (976 of 2158 = 45%) and the
    # worst-performing — so it appears in 2 of the 8 slots.
    CATEGORIES=(math physics chemistry biology "computer science" "humanities" engineering math)
    SLOT=$(( $(date -u +%H) % 8 ))
    CATEGORY=${CATEGORIES[$SLOT]}
fi
SEED=$(date -u +%s)  # fresh shuffle each batch

# Cheap health audit — if the previous batch's run dir has 0 results,
# something went wrong (balancer down, model crash, etc.). Log a warning
# so the operator notices in the babysitter log. We still fire a new
# batch — the loop is resilient to transient backend hiccups.
PREV_RUN=$(ls -td "$DRYDOCK/hle_results/run_"* 2>/dev/null | head -1)
if [ -n "$PREV_RUN" ] && [ -f "$PREV_RUN/results.jsonl" ]; then
    PREV_DONE=$(wc -l < "$PREV_RUN/results.jsonl" 2>/dev/null || echo 0)
    PREV_AGE_MIN=$(( ( $(date +%s) - $(stat -c %Y "$PREV_RUN") ) / 60 ))
    if [ "$PREV_DONE" -lt 2 ] && [ "$PREV_AGE_MIN" -lt 30 ]; then
        log "WARN — previous batch ($PREV_RUN) finished with only $PREV_DONE results in $PREV_AGE_MIN min — check balancer / model"
    fi
fi

# Log rotation — keep the last 14 days of per-batch logs. At 24
# batches/day that's ~336 files, well under any meaningful disk
# pressure. Deleted files are non-essential — results.jsonl lives
# under hle_results/run_<ts>/ and is the authoritative record.
find "$DRYDOCK/logs" -maxdepth 1 -name "hle_continuous_*.log" -mtime +14 \
    -delete 2>/dev/null

RUN_LOG="$DRYDOCK/logs/hle_continuous_$(date -u +%Y%m%d_%H%M%S).log"
log "starting batch limit=$LIMIT category=$CATEGORY seed=$SEED → $RUN_LOG"

# Point the model's retrieve / auto-prefetch at the arXiv corpus
# (1.18M chunks) instead of the default ~/.drydock/graphrag.sqlite,
# which contains test-project artifacts that aren't relevant to HLE
# Math/Physics/CS questions. 2026-05-14 trace: synthetic retrieve on
# a local-field-theory question pulled tool_agent/help_output.txt
# (score 38.7) because the project corpus was being searched.
# Operator override: set HLE_BABYSITTER_GRAPHRAG_DB explicitly (empty
# string disables the per-batch redirect entirely).
GRAPHRAG_DB_OVERRIDE=${HLE_BABYSITTER_GRAPHRAG_DB-/data3/arxiv_corpus/graphrag.sqlite}

cd "$DRYDOCK"
# PYTHONUNBUFFERED=1 + `-u` flag: line-buffered stdout so the per-Q
# progress lines land in RUN_LOG immediately instead of after the
# whole batch completes. Drydock_status reads from this log file.
if [ -n "$GRAPHRAG_DB_OVERRIDE" ]; then
    DRYDOCK_GRAPHRAG_DB="$GRAPHRAG_DB_OVERRIDE" \
    DRYDOCK_WRAP_UP_WARN_AT=8 \
    DRYDOCK_STOP_NOW_WARN_AT=12 \
    DRYDOCK_STOP_NOW_TIME_SEC=240 \
    DRYDOCK_THINKING_BUDGET_TOKENS=8000 \
    PYTHONUNBUFFERED=1 \
    nohup "$PY" -u "$DRYDOCK/scripts/hle_eval.py" \
        --source hle --limit "$LIMIT" --shuffle --seed "$SEED" --category "$CATEGORY" \
        > "$RUN_LOG" 2>&1 &
else
    DRYDOCK_WRAP_UP_WARN_AT=8 \
    DRYDOCK_STOP_NOW_WARN_AT=12 \
    DRYDOCK_STOP_NOW_TIME_SEC=240 \
    DRYDOCK_THINKING_BUDGET_TOKENS=8000 \
    PYTHONUNBUFFERED=1 \
    nohup "$PY" -u "$DRYDOCK/scripts/hle_eval.py" \
        --source hle --limit "$LIMIT" --shuffle --seed "$SEED" --category "$CATEGORY" \
        > "$RUN_LOG" 2>&1 &
fi
PID=$!
echo "$PID" > "$PID_FILE"
log "kicked off pid=$PID"
