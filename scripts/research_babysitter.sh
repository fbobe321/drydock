#!/bin/bash
# research_babysitter.sh — hourly overnight monitor for the Meta-Harness
# experimenter loop (research/experimenter.py --max-experiments 0).
#
# Designed to be called from cron:
#   15 * * * * /data3/drydock/scripts/research_babysitter.sh
#   (:15 so it doesn't collide with stress_babysitter at :00.)
#
# Responsibilities:
#  - Check the experimenter PID is alive.
#  - Log a single status line to /tmp/research_babysitter.log.
#  - If the PID is dead AND no STOP sentinel exists, restart the
#    experimenter with --max-experiments 0 --proposer local and log a
#    Telegram ping. Local proposer keeps the self-tuning loop air-gap-
#    safe; DO NOT switch this to --proposer opus (that contradicts the
#    self-hosted/regulated-environments product positioning).
#  - Telegram-notify only on STATE CHANGE (first death, restart,
#    clean stop via STOP sentinel). Healthy ticks go silent.
#
# Stop the loop by touching the sentinel:
#   touch /data3/drydock/research/STOP
# (same sentinel the experimenter itself watches.)

set -u

LOG=/tmp/research_babysitter.log
PID_FILE=/tmp/experimenter_pid.txt
EXP_LOG=/tmp/experimenter_overnight.log
STOP_SENTINEL=/data3/drydock/research/STOP
NOTIFY=/data3/drydock/scripts/notify_release.py
PY=/home/bobef/miniforge3/envs/drydock/bin/python3
EXPERIMENTER=/data3/drydock/research/experimenter.py
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

log() { echo "$NOW $*" >> "$LOG"; }

notify_on_change() {
    local key="$1"; shift
    local msg="$*"
    local flag="/tmp/research_babysitter.last_$key"
    if [ -f "$flag" ] && [ "$(cat "$flag" 2>/dev/null)" = "$msg" ]; then
        return 0
    fi
    echo "$msg" > "$flag"
    if [ -f "$NOTIFY" ]; then
        "$PY" "$NOTIFY" status "[research] $msg" >/dev/null 2>&1 || true
    fi
    log "TELEGRAM: $msg"
}

# Check user-visible STOP sentinel first. If it exists and experimenter
# isn't running, we respect it — no restart.
STOP_ACTIVE=0
[ -f "$STOP_SENTINEL" ] && STOP_ACTIVE=1

EXP_PID=""
[ -f "$PID_FILE" ] && EXP_PID=$(cat "$PID_FILE" 2>/dev/null)

# Count experiments produced so far + current best metric.
EXP_COUNT=$(($(wc -l < /data3/drydock/research/results.tsv 2>/dev/null) - 1))
[ "$EXP_COUNT" -lt 0 ] && EXP_COUNT=0
BEST_METRIC=$(awk -F'\t' 'NR>1 && $5 != "" {if($5+0 > m) m=$5+0} END {print m+0}' \
    /data3/drydock/research/results.tsv 2>/dev/null)
[ -z "$BEST_METRIC" ] && BEST_METRIC=0

if [ -n "$EXP_PID" ] && [ -d "/proc/$EXP_PID" ]; then
    ETIME=$(ps -p "$EXP_PID" -o etime= 2>/dev/null | xargs)
    RSS_KB=$(grep VmRSS /proc/"$EXP_PID"/status 2>/dev/null | awk '{print $2}')
    RSS_MB=$(( ${RSS_KB:-0} / 1024 ))
    log "tick pid=$EXP_PID alive=1 etime=$ETIME rss=${RSS_MB}MB experiments=$EXP_COUNT best=$BEST_METRIC stop=$STOP_ACTIVE"
    # Healthy — reset the dead-flag so a future death re-notifies.
    rm -f /tmp/research_babysitter.last_dead
    exit 0
fi

# --- experimenter is not running ---
log "tick pid=$EXP_PID alive=0 experiments=$EXP_COUNT best=$BEST_METRIC stop=$STOP_ACTIVE"

if [ "$STOP_ACTIVE" = "1" ]; then
    notify_on_change "stopped" "experimenter stopped (STOP sentinel present) after $EXP_COUNT exps, best=$BEST_METRIC"
    exit 0
fi

# --- restart ---
log "experimenter dead with no STOP sentinel — restarting"
NEW_LOG="/tmp/experimenter_overnight_restart_$(date +%s).log"
nohup "$PY" "$EXPERIMENTER" \
    --proposer local \
    --max-experiments 0 \
    --cooldown-s 30 \
    > "$NEW_LOG" 2>&1 &
NEW_PID=$!
disown 2>/dev/null || true
echo "$NEW_PID" > "$PID_FILE"
log "restart complete: new PID $NEW_PID log $NEW_LOG"
notify_on_change "restart" \
    "research experimenter died at $EXP_COUNT exps (best=$BEST_METRIC); restarted as PID $NEW_PID"

exit 0
