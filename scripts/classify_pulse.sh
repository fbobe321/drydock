#!/bin/bash
# classify_pulse.sh
#
# Periodic classifier runner. Reads recent log activity, classifies into
# buckets, dispatches to ~/.drydock/dispatch/<bucket>.jsonl queues for
# downstream automation (autonomous_review, GraphRAG curator, Deep Noir
# vector intake, LoRA candidate pile).
#
# Incremental: tracks the last-read line number for each source file in
# ~/.drydock/dispatch/.last_pos_<slug> so each run only classifies NEW
# lines. This prevents the queue from accumulating duplicate entries when
# the same 500-line window is re-classified every 10 minutes.
#
# Sources scanned:
#   /data3/drydock/logs/autonomous_review.log         (new lines since last run)
#   ~/.drydock/logs/admiral_history.log if present    (new lines since last run)
#
# Cron entry (when wired up):
#   */10 * * * * /data3/drydock/scripts/classify_pulse.sh

set -uo pipefail

DRYDOCK=/data3/drydock
PYTHON=/home/bobef/miniconda3/bin/python3
LOG="$DRYDOCK/logs/classify_pulse.log"
POS_DIR="$HOME/.drydock/dispatch"

mkdir -p "$DRYDOCK/logs"
exec >>"$LOG" 2>&1

echo "================ $(date -u +%FT%TZ) tick ================"

REVIEW_LOG="$DRYDOCK/logs/autonomous_review.log"
ADMIRAL_LOG="$HOME/.drydock/logs/admiral_history.log"

cd "$DRYDOCK" || exit 1

# scan_new_lines <log_file> <slug> <source_label>
# Reads only lines added since the last call. Updates the position file.
scan_new_lines() {
    local log_file="$1" slug="$2" source_label="$3"
    local pos_file="$POS_DIR/.last_pos_$slug"
    local last_line=0
    [ -f "$pos_file" ] && last_line=$(cat "$pos_file")
    local total_lines
    total_lines=$(wc -l < "$log_file")
    if [ "$total_lines" -le "$last_line" ]; then
        echo "  (no new lines since last run, skipping)"
        echo "$total_lines" > "$pos_file"
        return
    fi
    local new_count=$(( total_lines - last_line ))
    echo "  scanning $new_count new lines (lines $((last_line+1))–$total_lines)"
    tail -n "+$((last_line + 1))" "$log_file" | $PYTHON -m drydock.core.classifier - \
        --format summary \
        --source "$source_label" \
        --dispatch 2>&1 | tail -20
    echo "$total_lines" > "$pos_file"
}

if [ -f "$REVIEW_LOG" ]; then
    echo "--- autonomous_review.log ---"
    scan_new_lines "$REVIEW_LOG" "review" "autonomous_review.log"
fi

if [ -f "$ADMIRAL_LOG" ]; then
    echo ""
    echo "--- admiral_history.log ---"
    scan_new_lines "$ADMIRAL_LOG" "admiral" "admiral_history.log"
fi

echo ""
echo "--- queue depths ---"
for q in "$POS_DIR/"*.jsonl; do
    [ -f "$q" ] && printf "  %-40s %d\n" "$(basename "$q")" "$(wc -l < "$q")"
done

echo "done"
