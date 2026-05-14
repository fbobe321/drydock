#!/bin/bash
# hle_burndown_keepalive.sh — cron'd resurrector for the burndown daemon.
#
# Designed to be paranoid-safe in trip-mode: if the daemon process is
# alive, do nothing. If the PID file is missing or points at a dead
# PID, log and relaunch via hle_burndown.sh start. Anyone wanting to
# stop the loop must touch the pause file (or use the stop command,
# which removes the PID file).
#
# Cron line (already added):
#   */15 * * * * /data3/drydock/scripts/hle_burndown_keepalive.sh
#
# Files:
#   /tmp/hle_burndown.pid         — daemon PID
#   /tmp/hle_burndown_keepalive.log — keepalive's own append-only log
#   /data3/drydock/.pause_hle_burndown — pause sentinel; keepalive respects it
#   /data3/drydock/.pause_hle_burndown_keepalive — disable resurrection
#                                                  entirely (use for ad-hoc
#                                                  debugging)
set -u

DRYDOCK=/data3/drydock
LOG=/tmp/hle_burndown_keepalive.log
PID_FILE=/tmp/hle_burndown.pid

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# Disable entirely
if [ -f "$DRYDOCK/.pause_hle_burndown_keepalive" ]; then
    echo "[$(ts)] PAUSED via .pause_hle_burndown_keepalive" >> "$LOG"
    exit 0
fi

# Daemon alive? Done.
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    exit 0
fi

# Daemon dead but pause sentinel set → respect it, don't restart.
if [ -f "$DRYDOCK/.pause_hle_burndown" ]; then
    echo "[$(ts)] daemon dead but .pause_hle_burndown set — not restarting" >> "$LOG"
    exit 0
fi

# Stale PID file → log and clean up.
if [ -f "$PID_FILE" ]; then
    echo "[$(ts)] stale PID file (pid $(cat "$PID_FILE") dead) — removing" >> "$LOG"
    rm -f "$PID_FILE"
fi

# Restart the daemon.
echo "[$(ts)] resurrecting hle_burndown daemon" >> "$LOG"
/data3/drydock/scripts/hle_burndown.sh start >> "$LOG" 2>&1
