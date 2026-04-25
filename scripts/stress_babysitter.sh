#!/bin/bash
# stress_babysitter.sh — hourly overnight monitor for the v10 stress run.
#
# Designed to be called from cron:
#   0 * * * * /data3/drydock/scripts/stress_babysitter.sh
#
# Responsibilities:
#  - Check the stress harness PID is alive.
#  - Compute progress (done/skip/timeout/recycle) + pace since prior run.
#  - Log a single status line to /tmp/stress_babysitter.log (append-only).
#  - If the harness died AND progress < 1658, restart it from the last
#    known done count via --resume-from-step N. Relaunch the watcher.
#  - Telegram-notify only on STATE CHANGE (first death, restart success,
#    RSS blow-up). Healthy ticks go silent.
#
# Logs + PID files:
#   /tmp/stress_pid.txt        — current stress harness PID (updated on restart)
#   /tmp/stress_2000_v10.log   — harness stdout (renamed v10+ on each restart)
#   /tmp/stress_babysitter.log — this script's own log
#   /tmp/stress_babysitter.state — last-seen state (for change detection)

set -u

LOG=/tmp/stress_babysitter.log
STATE=/tmp/stress_babysitter.state
PID_FILE=/tmp/stress_pid.txt
# Autodetect the active stress log: most-recently-modified file matching
# /tmp/stress_2000_*.log. Avoids the stale-log bug where babysitter reported
# COMPLETE for hours while the real harness kept processing (2026-04-25).
# Falls back to the prior hardcoded path if no match exists yet.
STRESS_LOG=$(ls -t /tmp/stress_2000_*.log 2>/dev/null | head -1)
STRESS_LOG=${STRESS_LOG:-/tmp/stress_2000_v10_restart_1776736801.log}
NOTIFY=/data3/drydock/scripts/notify_release.py
PY_CONDA=/home/bobef/miniconda3/bin/python3
PY_DRYDOCK=/home/bobef/miniforge3/envs/drydock/bin/python3
PRD_CWD=/data3/drydock_test_projects/403_tool_agent
PROMPTS=/data3/drydock/scripts/stress_prompts_tool_agent_2000.txt
TOTAL_PROMPTS=1658
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

log() { echo "$NOW $*" >> "$LOG"; }

# Send a Telegram ping but ONLY if the message differs from the last
# sent one. Prevents hourly spam when a state (like "dead") persists.
notify_on_change() {
    local key="$1"; shift
    local msg="$*"
    local flag="/tmp/stress_babysitter.last_$key"
    if [ -f "$flag" ] && [ "$(cat "$flag" 2>/dev/null)" = "$msg" ]; then
        return 0
    fi
    echo "$msg" > "$flag"
    if [ -x "$NOTIFY" ] || [ -f "$NOTIFY" ]; then
        "$PY_CONDA" "$NOTIFY" status "[babysitter] $msg" >/dev/null 2>&1 || true
    fi
    log "TELEGRAM: $msg"
}

if [ ! -f "$PID_FILE" ]; then
    log "no PID file at $PID_FILE — nothing to monitor"
    notify_on_change "nopid" "no PID file — stress run not tracked"
    exit 0
fi
STRESS_PID=$(cat "$PID_FILE")

# --- pull stats from the log (using the grep pattern that ACTUALLY matches
# the harness's 13-space "done:" indent, not the 10-space one I got wrong
# earlier today). ---
if [ -f "$STRESS_LOG" ]; then
    DONE=$(grep -cE 'done: \+[0-9]+ msgs' "$STRESS_LOG" 2>/dev/null)
    SKIP=$(grep -c 'SKIP: TUI did not accept' "$STRESS_LOG" 2>/dev/null)
    TIMEOUT=$(grep -cE 'TIMEOUT: \+[0-9]+ msgs' "$STRESS_LOG" 2>/dev/null)
    RECYCLE=$(grep -c 'RECYCLE-TUI' "$STRESS_LOG" 2>/dev/null)
    CURIDX=$(grep -oE "^\[\s*[0-9]+/" "$STRESS_LOG" 2>/dev/null \
        | tail -1 | grep -oE "[0-9]+")
    CURIDX=${CURIDX:-0}
else
    DONE=0; SKIP=0; TIMEOUT=0; RECYCLE=0; CURIDX=0
fi

# --- alive check ---
if [ -d /proc/"$STRESS_PID" ]; then
    RSS_KB=$(grep VmRSS /proc/"$STRESS_PID"/status 2>/dev/null | awk '{print $2}')
    RSS_MB=$(( ${RSS_KB:-0} / 1024 ))
    ETIME=$(ps -p "$STRESS_PID" -o etime= 2>/dev/null | xargs)
    ALIVE=1
else
    RSS_MB=0
    ETIME="--"
    ALIVE=0
fi

log "tick pid=$STRESS_PID alive=$ALIVE etime=$ETIME rss=${RSS_MB}MB done=$DONE skip=$SKIP timeout=$TIMEOUT recycle=$RECYCLE idx=$CURIDX/$TOTAL_PROMPTS"

# --- healthy path ---
if [ "$ALIVE" = "1" ]; then
    # Reset the dead flag so a later death re-notifies.
    rm -f /tmp/stress_babysitter.last_dead
    # Flag very high RSS (past the 2GB mark).
    if [ "$RSS_MB" -gt 2000 ]; then
        notify_on_change "rss" "RSS high: ${RSS_MB}MB — admiral actuator should kick in at 4GB"
    fi
    # Completion check
    if [ "$CURIDX" -ge "$TOTAL_PROMPTS" ]; then
        notify_on_change "complete" "stress run COMPLETE: done=$DONE skip=$SKIP timeout=$TIMEOUT"
    fi
    exit 0
fi

# --- death path: harness is gone ---
log "harness PID $STRESS_PID is DEAD (etime=$ETIME)"

# If the run had already completed, don't restart.
if [ "$CURIDX" -ge "$TOTAL_PROMPTS" ]; then
    notify_on_change "complete_dead" "stress run completed and harness exited: done=$DONE"
    exit 0
fi

# Determine restart step. DONE is the count of successful prompts; feed
# that into --resume-from-step so the restart continues where the last
# accepted prompt left off.
RESUME_AT=$DONE
if [ "$RESUME_AT" -lt 1 ]; then
    RESUME_AT=1
fi

log "restarting from --resume-from-step $RESUME_AT"
NEW_LOG=/tmp/stress_2000_v10_restart_$(date +%s).log

nohup "$PY_CONDA" -u /data3/drydock/scripts/stress_shakedown.py \
    --cwd "$PRD_CWD" \
    --pkg tool_agent \
    --prompts "$PROMPTS" \
    --max-per-prompt 300 --report-every 25 \
    --resume-from-step "$RESUME_AT" \
    > "$NEW_LOG" 2>&1 &
NEW_PID=$!
disown 2>/dev/null || true
echo "$NEW_PID" > "$PID_FILE"

# Relaunch the watcher on the new log and new PID.
nohup "$PY_DRYDOCK" /data3/drydock/scripts/stress_watcher.py \
    --log "$NEW_LOG" --pid "$NEW_PID" --stall-threshold 900 \
    > "/tmp/stress_watcher_v10_restart_$(date +%s).log" 2>&1 &
disown 2>/dev/null || true

# Point the monitor at the new log for subsequent ticks.
ln -sf "$NEW_LOG" /tmp/stress_2000_v10.log.current
sed -i "s|^STRESS_LOG=.*|STRESS_LOG=$NEW_LOG|" "$0" 2>/dev/null || true

log "restart complete: new PID $NEW_PID log $NEW_LOG"
notify_on_change "restart" \
    "restarted stress run: was at $CURIDX/$TOTAL_PROMPTS, resuming from step $RESUME_AT, new PID $NEW_PID"

exit 0
