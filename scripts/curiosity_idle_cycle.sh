#!/bin/bash
# curiosity_idle_cycle.sh — bounded exploration when drydock is genuinely idle.
#
# SOVEREIGN_PRD §5.7 tier-3 forcing function. Runs from cron once per hour;
# checks idle conditions; if met, picks the top curiosity item and writes a
# hypothesis to ~/.drydock/curiosity_log.md. No model call, no commits —
# just structured notes the operator (or the next autonomous_review tick)
# can act on. The point is producing self-directed exploration that
# accumulates over a quiet weekend, not racing through it on a budget.
#
# Cron suggestion:
#   17 * * * * /data3/drydock/scripts/curiosity_idle_cycle.sh >> \
#               /data3/drydock/logs/curiosity_idle.log 2>&1
#
# Idle conditions (ALL must hold):
#   - No drydock TUI process running (don't compete with the user)
#   - No HLE eval running
#   - No autonomous_review running (covered by its own lock, but double-check)
#   - At least one pending UNCONSUMED curiosity item exists
#   - Daily budget not exhausted (tracked in ~/.drydock/.curiosity_idle_state.json)

set -u

DRYDOCK=/data3/drydock
LOG_DIR="$DRYDOCK/logs"
LOG_FILE="$DRYDOCK/curiosity_log.md"
STATE_FILE="$HOME/.drydock/.curiosity_idle_state.json"
PAUSE_FILE="$DRYDOCK/.pause_curiosity_idle"
PY=/home/bobef/miniconda3/bin/python3
DAILY_CAP=12  # max idle cycles per UTC day

mkdir -p "$LOG_DIR" "$(dirname "$STATE_FILE")"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
today() { date -u +"%Y-%m-%d"; }

log() { echo "[$(ts)] $*"; }

# --- Pause sentinel ---
if [ -f "$PAUSE_FILE" ]; then
    log "PAUSED via $PAUSE_FILE"
    exit 0
fi

# --- Idle gate: HLE eval (checked FIRST — HLE spawns child drydock TUI
# processes, so the TUI check below would always misfire while HLE runs) ---
if pgrep -fa "hle_eval.py\b" >/dev/null 2>&1; then
    log "SKIP — HLE eval is running"
    exit 0
fi
if pgrep -fa "autonomous_review.sh\b" >/dev/null 2>&1; then
    log "SKIP — autonomous_review is mid-run"
    exit 0
fi

# --- Idle gate: drydock TUI ---
# Only count the user's TUI: launched via `drydock` or `python -m drydock`.
# The pkill-by-name family of false positives is exactly the bug
# memory/feedback_never_kill_drydock.md warns against — match strictly.
if pgrep -fa "python.*-m drydock\b|/bin/drydock\b" >/dev/null 2>&1; then
    log "SKIP — drydock TUI is active"
    exit 0
fi

# --- Daily budget check ---
DAY=$(today)
DAILY_COUNT=$($PY - "$STATE_FILE" "$DAY" <<'EOF'
import json, sys
state_file, day = sys.argv[1], sys.argv[2]
try:
    s = json.load(open(state_file))
except Exception:
    s = {}
print(s.get("by_day", {}).get(day, 0))
EOF
)
DAILY_COUNT=${DAILY_COUNT:-0}
if [ "$DAILY_COUNT" -ge "$DAILY_CAP" ]; then
    log "SKIP — daily budget exhausted ($DAILY_COUNT/$DAILY_CAP)"
    exit 0
fi

# --- Anything in the queue? ---
PENDING=$($PY -m drydock.curiosity stats 2>/dev/null | awk '/Total:/ {for(i=1;i<=NF;i++) if($i=="pending") print $(i-1)}' | head -1)
PENDING=${PENDING:-0}
if [ "$PENDING" -eq 0 ]; then
    log "SKIP — no pending curiosity items"
    exit 0
fi

# --- All idle conditions met → pick top item, log a hypothesis ---
TOP_OUTPUT=$($PY -m drydock.curiosity top --limit 1 2>/dev/null || true)
if [ -z "$TOP_OUTPUT" ]; then
    log "SKIP — top returned nothing (unexpected with pending=$PENDING)"
    exit 0
fi

TOP_ID=$(echo "$TOP_OUTPUT" | grep -oP '`[0-9a-f]{16}`' | head -1 | tr -d '`')
if [ -z "$TOP_ID" ]; then
    log "SKIP — could not extract top item id"
    exit 0
fi

# Append the hypothesis section to curiosity_log.md
{
    echo ""
    echo "## $(ts) — idle exploration tick"
    echo ""
    echo "**Idle conditions met:** no TUI, no HLE, no autonomous_review."
    echo "**Pending items:** $PENDING"
    echo "**Budget:** $DAILY_COUNT/$DAILY_CAP for $DAY"
    echo ""
    echo "**Top item picked:** \`$TOP_ID\`"
    echo ""
    echo "$TOP_OUTPUT"
    echo ""
    echo "_This is a passive log entry. The next \`autonomous_review\` tick"
    echo "will see this item via \`/curiosity top\` and decide whether to"
    echo "act on it. To force action, run:_"
    echo ""
    echo '```'
    echo "bash /data3/drydock/scripts/autonomous_review.sh"
    echo '```'
} >> "$LOG_FILE"

# Update daily counter
$PY - "$STATE_FILE" "$DAY" <<'EOF'
import json, sys
state_file, day = sys.argv[1], sys.argv[2]
try:
    s = json.load(open(state_file))
except Exception:
    s = {}
by_day = s.setdefault("by_day", {})
by_day[day] = by_day.get(day, 0) + 1
# Keep last 14 days only.
keep = sorted(by_day.keys())[-14:]
s["by_day"] = {k: by_day[k] for k in keep}
s["last_run"] = day
with open(state_file, "w") as f:
    json.dump(s, f, indent=2, sort_keys=True)
EOF

log "OK — logged hypothesis for $TOP_ID ($((DAILY_COUNT + 1))/$DAILY_CAP today)"
exit 0
