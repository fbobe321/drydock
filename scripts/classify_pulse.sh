#!/bin/bash
# classify_pulse.sh
#
# Periodic classifier runner. Reads recent log activity, classifies into
# buckets, dispatches to ~/.drydock/dispatch/<bucket>.jsonl queues for
# downstream automation (autonomous_review, GraphRAG curator, Deep Noir
# vector intake, LoRA candidate pile).
#
# Idempotent: dispatcher dedups (pattern_id, evidence) per run, so
# re-classifying the same lines is cheap and doesn't bloat queues.
#
# Sources scanned:
#   /data3/drydock/logs/autonomous_review.log         (last 500 lines)
#   ~/.drydock/logs/admiral_history.log if present    (last 500 lines)
#
# Cron entry (when wired up):
#   */10 * * * * /data3/drydock/scripts/classify_pulse.sh

set -uo pipefail

DRYDOCK=/data3/drydock
PYTHON=/home/bobef/miniconda3/bin/python3
LOG="$DRYDOCK/logs/classify_pulse.log"

mkdir -p "$DRYDOCK/logs"
exec >>"$LOG" 2>&1

echo "================ $(date -u +%FT%TZ) tick ================"

REVIEW_LOG="$DRYDOCK/logs/autonomous_review.log"
ADMIRAL_LOG="$HOME/.drydock/logs/admiral_history.log"

cd "$DRYDOCK" || exit 1

if [ -f "$REVIEW_LOG" ]; then
    echo "--- autonomous_review.log ---"
    tail -500 "$REVIEW_LOG" | $PYTHON -m drydock.core.classifier - \
        --format summary \
        --source autonomous_review.log \
        --dispatch 2>&1 | tail -20
fi

if [ -f "$ADMIRAL_LOG" ]; then
    echo ""
    echo "--- admiral_history.log ---"
    tail -500 "$ADMIRAL_LOG" | $PYTHON -m drydock.core.classifier - \
        --format summary \
        --source admiral_history.log \
        --dispatch 2>&1 | tail -20
fi

echo ""
echo "--- queue depths ---"
for q in "$HOME/.drydock/dispatch/"*.jsonl; do
    [ -f "$q" ] && printf "  %-40s %d\n" "$(basename "$q")" "$(wc -l < "$q")"
done

echo "done"
