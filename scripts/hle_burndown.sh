#!/bin/bash
# hle_burndown.sh — keep the GPUs saturated burning down the 2500-Q HLE set.
#
# The hourly babysitter cron (hle_babysitter.sh) wastes 30-50% of the
# wall clock because each batch finishes in 30-80 min but the next
# kickoff waits for the top of the hour. This daemon runs back-to-back
# batches with zero idle gap, while coexisting peacefully with the
# babysitter (both use /tmp/hle_continuous.pid; the babysitter will
# see a live PID and exit each tick).
#
# Design choices:
#   - Single batch at a time (one llama.cpp slot on remus).
#   - Category rotation across all 8 HLE categories, weighted by
#     dataset size (Math 1021 / 2500 ≈ 40%, etc.) so we don't
#     over-sample tiny categories.
#   - Same env knobs as the babysitter (DRYDOCK_WRAP_UP_WARN_AT,
#     STOP_NOW_TIME_SEC, GraphRAG DB redirect, thinking budget).
#   - Skip a tick if the stress harness or autonomous_review owns
#     the slot — the same coexistence rules the babysitter uses.
#   - Pause via /data3/drydock/.pause_hle_burndown.
#   - Stop via SIGTERM (PID at /tmp/hle_burndown.pid).
#
# Usage:
#   nohup /data3/drydock/scripts/hle_burndown.sh > /tmp/hle_burndown.log 2>&1 &
#   echo $! > /tmp/hle_burndown.pid
#
# Or launch with `start`:
#   /data3/drydock/scripts/hle_burndown.sh start
#
# Status:
#   /data3/drydock/scripts/hle_burndown.sh status
#
# Stop:
#   /data3/drydock/scripts/hle_burndown.sh stop

set -u

DRYDOCK=/data3/drydock
LOG=/tmp/hle_burndown.log
PID_FILE=/tmp/hle_burndown.pid
BATCH_PID_FILE=/tmp/hle_continuous.pid     # shared with babysitter
PAUSE_FILE="$DRYDOCK/.pause_hle_burndown"
PY=/home/bobef/miniconda3/bin/python3

LIMIT=${HLE_BURNDOWN_LIMIT:-10}
# Hard ceiling per batch — kill if exceeded. Each Q ≤ 480s; a 10-Q
# batch should never exceed ~5400s. 2h gives us margin.
BATCH_TIMEOUT_SEC=${HLE_BURNDOWN_BATCH_TIMEOUT_SEC:-7200}
# Polling interval while waiting for a batch to finish (seconds).
POLL_SEC=${HLE_BURNDOWN_POLL_SEC:-30}
# Brief settle pause between batches so the GPU memory unstinks before
# the next batch's first request. Keep small (≤30s) so we don't bleed
# throughput.
SETTLE_SEC=${HLE_BURNDOWN_SETTLE_SEC:-15}
# GraphRAG corpus redirect (arXiv) — same default as babysitter.
GRAPHRAG_DB=${HLE_BURNDOWN_GRAPHRAG_DB-/data3/arxiv_corpus/graphrag.sqlite}

# Category rotation weighted by HLE dataset size:
#   Math 1021 (40.8%), Bio 280 (11.2%), CS 241 (9.6%), Other 233 (9.3%),
#   Physics 230 (9.2%), Humanities 219 (8.8%), Chem 165 (6.6%), Eng 111 (4.4%).
# Use 20 slots so Math gets 8 = 40%, the rest scaled accordingly.
CATEGORIES=(
    math math math math math math math math
    "biology/medicine" "biology/medicine"
    "computer science"  "computer science"
    other other
    physics physics
    humanities
    chemistry
    engineering
    math
)

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
# stdout is already captured to $LOG via the daemon's nohup redirect,
# so just echo — no tee (otherwise every line is duplicated).
log() { echo "[$(ts)] $*"; }

# ── Lifecycle commands ───────────────────────────────────────────────

start_daemon() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "burndown already running (pid $(cat "$PID_FILE"))"
        exit 0
    fi
    # Re-exec self in nohup mode so we survive shell exit.
    nohup "$0" run >> "$LOG" 2>&1 &
    echo $! > "$PID_FILE"
    echo "burndown started pid=$(cat "$PID_FILE") log=$LOG"
}

stop_daemon() {
    if [ ! -f "$PID_FILE" ]; then
        echo "no PID file"
        return 0
    fi
    P=$(cat "$PID_FILE")
    if kill -0 "$P" 2>/dev/null; then
        kill "$P"
        echo "sent SIGTERM to pid=$P"
    fi
    rm -f "$PID_FILE"
}

status_daemon() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "daemon: alive (pid $(cat "$PID_FILE"))"
    else
        echo "daemon: dead"
    fi
    if [ -f "$BATCH_PID_FILE" ] && kill -0 "$(cat "$BATCH_PID_FILE")" 2>/dev/null; then
        echo "batch:  alive (pid $(cat "$BATCH_PID_FILE"))"
    else
        echo "batch:  none"
    fi
    if [ -f "$PAUSE_FILE" ]; then
        echo "PAUSED via $PAUSE_FILE"
    fi
    # Show batch count from log
    if [ -f "$LOG" ]; then
        BATCHES=$(grep -c "starting batch" "$LOG" 2>/dev/null || echo 0)
        DONE=$(grep -c "batch complete" "$LOG" 2>/dev/null || echo 0)
        echo "batches dispatched=$BATCHES, completed=$DONE"
    fi
}

# ── Main loop ────────────────────────────────────────────────────────

run_loop() {
    log "burndown loop started — limit=$LIMIT, settle=${SETTLE_SEC}s, poll=${POLL_SEC}s"

    trap 'log "received SIGTERM — shutting down"; exit 0' TERM
    trap 'log "received SIGINT — shutting down"; exit 0' INT

    local slot=0
    while true; do
        # Pause check
        if [ -f "$PAUSE_FILE" ]; then
            log "PAUSED via $PAUSE_FILE — sleeping 60s before re-check"
            sleep 60
            continue
        fi

        # Coexist with any HLE batch already running — could be from a
        # previous incarnation of this daemon, the hourly babysitter, or
        # a manually-launched ad-hoc batch. We MUST wait for it to clear
        # before launching another or we'll have two clients fighting
        # for the single llama.cpp slot. Polled wait with the same hard
        # timeout as our own batches.
        if [ -f "$BATCH_PID_FILE" ] && kill -0 "$(cat "$BATCH_PID_FILE")" 2>/dev/null; then
            EXISTING=$(cat "$BATCH_PID_FILE")
            log "waiting on pre-existing HLE batch pid=$EXISTING before starting new one"
            local wait_start=$(date +%s)
            while kill -0 "$EXISTING" 2>/dev/null; do
                sleep "$POLL_SEC"
                local age=$(( $(date +%s) - wait_start ))
                if [ "$age" -gt "$BATCH_TIMEOUT_SEC" ]; then
                    log "pre-existing batch pid=$EXISTING exceeded ${BATCH_TIMEOUT_SEC}s — killing"
                    kill "$EXISTING" 2>/dev/null
                    sleep 5
                    kill -9 "$EXISTING" 2>/dev/null
                    break
                fi
            done
            rm -f "$BATCH_PID_FILE"
            log "pre-existing batch cleared, continuing loop"
        fi

        # Coexist: skip if stress harness or autonomous_review owns the slot.
        STRESS_PID_FILE=/tmp/stress_pid.txt
        if [ -f "$STRESS_PID_FILE" ] && kill -0 "$(cat "$STRESS_PID_FILE")" 2>/dev/null; then
            log "skip — stress run in progress (pid $(cat "$STRESS_PID_FILE")) — sleep 5m"
            sleep 300
            continue
        fi
        if pgrep -fa "autonomous_review\.sh\b" >/dev/null 2>&1; then
            log "skip — autonomous_review mid-run — sleep 90s"
            sleep 90
            continue
        fi

        # Balancer health
        if ! curl -fs --max-time 5 http://127.0.0.1:8001/v1/models >/dev/null 2>&1; then
            log "skip — balancer :8001 unhealthy — sleep 60s"
            sleep 60
            continue
        fi

        # Pick category and fire batch
        CAT="${CATEGORIES[$((slot % ${#CATEGORIES[@]}))]}"
        slot=$((slot + 1))
        SEED=$(date -u +%s)
        RUN_LOG="$DRYDOCK/logs/hle_burndown_$(date -u +%Y%m%d_%H%M%S).log"
        log "starting batch slot=$slot limit=$LIMIT category=$CAT seed=$SEED → $RUN_LOG"

        cd "$DRYDOCK"
        if [ -n "$GRAPHRAG_DB" ]; then
            DRYDOCK_GRAPHRAG_DB="$GRAPHRAG_DB" \
            DRYDOCK_WRAP_UP_WARN_AT=8 \
            DRYDOCK_STOP_NOW_WARN_AT=12 \
            DRYDOCK_STOP_NOW_TIME_SEC=240 \
            DRYDOCK_THINKING_BUDGET_TOKENS=8000 \
            PYTHONUNBUFFERED=1 \
            nohup "$PY" -u "$DRYDOCK/scripts/hle_eval.py" \
                --source hle --limit "$LIMIT" --shuffle --seed "$SEED" \
                --category "$CAT" \
                > "$RUN_LOG" 2>&1 &
        else
            DRYDOCK_WRAP_UP_WARN_AT=8 \
            DRYDOCK_STOP_NOW_WARN_AT=12 \
            DRYDOCK_STOP_NOW_TIME_SEC=240 \
            DRYDOCK_THINKING_BUDGET_TOKENS=8000 \
            PYTHONUNBUFFERED=1 \
            nohup "$PY" -u "$DRYDOCK/scripts/hle_eval.py" \
                --source hle --limit "$LIMIT" --shuffle --seed "$SEED" \
                --category "$CAT" \
                > "$RUN_LOG" 2>&1 &
        fi
        BATCH_PID=$!
        echo "$BATCH_PID" > "$BATCH_PID_FILE"
        log "batch pid=$BATCH_PID kicked off"

        # Wait for batch to finish, with a hard timeout safety net.
        local start_t=$(date +%s)
        while kill -0 "$BATCH_PID" 2>/dev/null; do
            sleep "$POLL_SEC"
            local now=$(date +%s)
            local age=$((now - start_t))
            if [ "$age" -gt "$BATCH_TIMEOUT_SEC" ]; then
                log "batch pid=$BATCH_PID exceeded ${BATCH_TIMEOUT_SEC}s — killing"
                kill "$BATCH_PID" 2>/dev/null
                sleep 5
                kill -9 "$BATCH_PID" 2>/dev/null
                break
            fi
            # Re-check pause file mid-batch so the operator can interrupt
            # the loop without waiting for the current batch to end.
            if [ -f "$PAUSE_FILE" ]; then
                log "pause requested mid-batch — letting current batch finish"
                # Don't kill the current batch; just stop the loop after.
                wait "$BATCH_PID" 2>/dev/null
                log "PAUSED via $PAUSE_FILE"
                while [ -f "$PAUSE_FILE" ]; do
                    sleep 60
                done
                break
            fi
        done

        # Per-batch summary
        local lifetime=$(grep -c "RESULT" "$RUN_LOG" 2>/dev/null || echo 0)
        local correct=$(grep -E "→ (YES|PARTIAL)" "$RUN_LOG" 2>/dev/null | wc -l)
        log "batch complete slot=$slot pid=$BATCH_PID category=$CAT correct=$correct/$lifetime"

        rm -f "$BATCH_PID_FILE"
        sleep "$SETTLE_SEC"
    done
}

# ── Entrypoint ───────────────────────────────────────────────────────

case "${1:-run}" in
    start)  start_daemon ;;
    stop)   stop_daemon ;;
    status) status_daemon ;;
    run)    run_loop ;;
    *)
        echo "usage: $0 {start|stop|status|run}"
        exit 1
        ;;
esac
