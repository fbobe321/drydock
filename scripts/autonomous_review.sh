#!/bin/bash
# autonomous_review.sh — cron-launched Claude Code wrapper that continues the
# drydock improvement loop while the user is on a trip.
#
# Designed to be called from cron:
#   */30 * * * * /data3/drydock/scripts/autonomous_review.sh
#
# Behavior:
#   - Pause sentinel: /data3/drydock/.pause_autonomous_loop → exit silently
#   - flock prevents overlapping runs (if previous claude is still working)
#   - Hard timeout via `timeout` command (15 min cap)
#   - Per-run cost cap via --max-budget-usd
#   - All output appended to /data3/drydock/logs/autonomous_review.log
#   - Trip log at /data3/drydock/trip_log.md is the human-readable summary

set -u

DRYDOCK=/data3/drydock
PAUSE_FILE="$DRYDOCK/.pause_autonomous_loop"
LOG_DIR="$DRYDOCK/logs"
RUN_LOG="$LOG_DIR/autonomous_review.log"
TRIP_LOG="$DRYDOCK/trip_log.md"
LOCK_FILE=/tmp/autonomous_review.lock
PROMPT_FILE="$DRYDOCK/scripts/autonomous_review_prompt.md"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

mkdir -p "$LOG_DIR"

# --- Pause check ---
if [ -f "$PAUSE_FILE" ]; then
    echo "$NOW PAUSED via $PAUSE_FILE — exiting" >> "$RUN_LOG"
    exit 0
fi

# --- Lock to prevent overlapping runs ---
exec 9> "$LOCK_FILE"
if ! flock -n 9; then
    echo "$NOW SKIP — previous run still active" >> "$RUN_LOG"
    exit 0
fi

# --- Pre-flight: ensure drydock directory exists and is git-clean enough ---
cd "$DRYDOCK" || { echo "$NOW FATAL — cannot cd $DRYDOCK" >> "$RUN_LOG"; exit 1; }

# --- Build the prompt argument ---
if [ ! -f "$PROMPT_FILE" ]; then
    echo "$NOW FATAL — prompt file missing: $PROMPT_FILE" >> "$RUN_LOG"
    exit 1
fi

# --- Run claude with safety rails ---
# - timeout 15m: hard kill if Claude runs too long
# - --print: non-interactive, exits when done
# - --dangerously-skip-permissions: required for cron (no human to approve)
# - --max-budget-usd 0.50: cost cap per run
# - --model sonnet: faster + cheaper than opus, plenty for status review + small fixes
# - --effort medium: balance of quality vs speed
# - --no-session-persistence: don't pollute session history
# - --output-format text: human-readable for the run log
# - --add-dir: explicit allow for the drydock tree

echo "" >> "$RUN_LOG"
echo "================ $NOW START ================" >> "$RUN_LOG"

PROMPT_TEXT=$(cat "$PROMPT_FILE")

PATH=/home/bobef/.local/bin:/home/bobef/miniconda3/bin:$PATH
export PATH

timeout --signal=TERM --kill-after=30s 15m \
    claude \
        --print \
        --dangerously-skip-permissions \
        --max-budget-usd 1.00 \
        --model sonnet \
        --effort medium \
        --no-session-persistence \
        --output-format text \
        --add-dir "$DRYDOCK" \
        --append-system-prompt "You are a cron-launched assistant. Be concise. Follow the rules in the user's prompt strictly. Default to writing a status line in trip_log.md rather than making speculative changes." \
        "$PROMPT_TEXT" \
    >> "$RUN_LOG" 2>&1

EXIT_CODE=$?
END_NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "" >> "$RUN_LOG"
echo "================ $END_NOW END exit=$EXIT_CODE ================" >> "$RUN_LOG"

# --- Telegram notify on bad exit (timeout, crash) ---
NOTIFY="$DRYDOCK/scripts/notify_release.py"
if [ "$EXIT_CODE" != "0" ] && [ -f "$NOTIFY" ]; then
    /home/bobef/miniconda3/bin/python3 "$NOTIFY" status \
        "[autonomous_review] non-zero exit: $EXIT_CODE at $END_NOW (timeout=124, oom=137)" \
        >/dev/null 2>&1 || true
fi

exit 0
