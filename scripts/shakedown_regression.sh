#!/bin/bash
# Full regression: run shakedown.py against EVERY PRD in
# /data3/drydock_test_projects/ that has a PRD.md.
#
# Each test resets PRD.md from PRD.master.md (snapshot before first
# run) so contamination from one run can't poison the next.
#
# Output:
#   /tmp/shakedown_regression_results.txt   per-project pass/fail
#   /tmp/shakedown_regression_log/<proj>.log  full log per project
#
# Designed to run for hours (overnight). Reads the package name from
# the project's PRD.md if it has a `## Package Name` section, else
# strips the leading number from the dir name.

set -uo pipefail

PYTHON="/home/bobef/miniconda3/bin/python3"
HARNESS="/data3/drydock/scripts/shakedown.py"
PROJECTS_DIR="/data3/drydock_test_projects"
RESULTS="/tmp/shakedown_regression_results.txt"
LOG_DIR="/tmp/shakedown_regression_log"
START_FROM="${1:-1}"   # optional: start from a specific project number

mkdir -p "$LOG_DIR"
# Don't truncate results — append. Lets us resume after interruption.
touch "$RESULTS"

# Build a set of already-completed projects (so we can skip them on resume)
declare -A already_done=()
if [ -s "$RESULTS" ]; then
    while IFS='|' read -r p _v _t; do
        p=$(echo "$p" | xargs)
        [ -n "$p" ] && already_done["$p"]=1
    done < "$RESULTS"
fi
echo "Resuming: ${#already_done[@]} projects already done from previous run"

# Pause the watchdog and auto_release crons so they don't fight us
touch /data3/drydock_test_projects/.pause_watchdog
touch /data3/drydock/.pause_auto_release

cleanup() {
    # Don't auto-unpause — let the user decide in the morning
    echo "[$(date)] regression run interrupted/completed"
}
trap cleanup EXIT

# Build the project list
projs=()
for d in "$PROJECTS_DIR"/[0-9]*/; do
    proj=$(basename "$d")
    [ -f "$d/PRD.md" ] || continue
    # Skip projects below START_FROM
    num=$(echo "$proj" | grep -oP '^\d+')
    if [ -n "$num" ] && [ "$num" -lt "$START_FROM" ]; then
        continue
    fi
    projs+=("$proj")
done

total=${#projs[@]}
pass=0
fail=0
done_n=0

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  SHAKEDOWN REGRESSION — $total projects (starting from $START_FROM)"
echo "║  Output: $RESULTS"
echo "║  Per-project logs: $LOG_DIR/<proj>.log"
echo "╚═══════════════════════════════════════════════════════════╝"
date

for proj in "${projs[@]}"; do
    # Skip if already done in a previous run
    if [ -n "${already_done[$proj]:-}" ]; then
        continue
    fi
    dir="$PROJECTS_DIR/$proj"
    done_n=$((done_n + 1))

    # Derive package name from the PRD's "## Package Name" section if it
    # exists, otherwise fall back to stripping the leading number from the
    # directory name. Many PRDs use a different package name than the dir
    # (e.g., dir=08_todo_list but PRD says `todo_manager`).
    pkg=$(grep -A1 "## Package Name" "$dir/PRD.md" 2>/dev/null \
        | tail -1 | grep -oP '`\K[a-z0-9_]+(?=`)' | head -1)
    [ -z "$pkg" ] && pkg=$(echo "$proj" | sed 's/^[0-9]*_//')

    # Snapshot PRD if no master exists yet
    if [ -f "$dir/PRD.md" ] && [ ! -f "$dir/PRD.master.md" ]; then
        cp "$dir/PRD.md" "$dir/PRD.master.md"
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "[$done_n/$total] $proj ($pkg) — $(date '+%H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    PYTHONUNBUFFERED=1 "$PYTHON" -u "$HARNESS" \
        --cwd "$dir" \
        --prompt "review the PRD and build the package" \
        --pkg "$pkg" \
        > "$LOG_DIR/$proj.log" 2>&1
    rc=$?

    if [ $rc -eq 0 ]; then
        pass=$((pass + 1))
        verdict="PASS"
        echo "  → PASS"
    else
        fail=$((fail + 1))
        verdict="FAIL"
        # Surface the fail reasons from the log
        grep "  - " "$LOG_DIR/$proj.log" | head -3 | sed 's/^/    /'
    fi
    echo "$proj | $verdict | $(date '+%H:%M:%S')" >> "$RESULTS"

    # Periodic progress
    if [ $((done_n % 10)) -eq 0 ]; then
        pct=$((pass * 100 / done_n))
        echo ""
        echo "──── PROGRESS: $done_n/$total — $pass pass, $fail fail ($pct%) ────"
    fi
done

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
pct=$((pass * 100 / total))
echo "║  REGRESSION COMPLETE: $pass/$total PASS ($pct%)"
echo "╚═══════════════════════════════════════════════════════════╝"
date
echo ""
echo "Results: $RESULTS"
echo "Per-project logs: $LOG_DIR/"

# Telegram notification if available
if [ -x /data3/drydock/scripts/notify_release.py ]; then
    "$PYTHON" /data3/drydock/scripts/notify_release.py "shakedown-regression" \
        "Regression: $pass/$total PASS ($pct%). Logs in $LOG_DIR" 2>/dev/null || true
fi
