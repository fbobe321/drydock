#!/usr/bin/env bash
# tui_canvas_bigfile_test.sh — verify the Document Canvas edits a LARGE file
# through the REAL drydock TUI (not just unit tests). Drives the deterministic
# /doc slash command (open → search → global replace → commit) in a tmux TUI and
# checks the on-disk result. /doc makes no model calls, so no model server is
# needed and it won't disturb any running agent workload.
#
# Usage: scripts/tui_canvas_bigfile_test.sh [path-to-large-doc]
#   With no arg it fetches War and Peace (~3.3 MB / ~1100 pages) from Gutenberg.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${DRYDOCK_PY:-python3}"
WORK="$(mktemp -d)"
HOME_ISO="$WORK/home"; mkdir -p "$HOME_ISO"
SESSION="canvas_bigfile_$$"
SRC="${1:-}"

if [ -z "$SRC" ]; then
    SRC="$WORK/warandpeace.txt"
    echo "==> fetching a large public-domain document…"
    curl -s -m 60 -L -o "$SRC" https://www.gutenberg.org/files/2600/2600-0.txt || {
        echo "FAIL: could not fetch the sample document"; exit 1; }
fi
cp "$SRC" "$WORK/big.txt"; SRC="$WORK/big.txt"
NAME="big.txt"
bytes=$(stat -c %s "$SRC"); echo "==> document: $NAME ($bytes bytes, $(wc -l <"$SRC") lines)"

# grep -c already prints 0 on no match (exit 1); the trailing ':' keeps the
# command-substitution exit clean so a zero count doesn't emit a second line.
count() { grep -c "$1" "$2" 2>/dev/null; :; }
FROM="Napoleon"; TO="NAPOLEON_REDACTED"
before=$(count "$FROM" "$SRC")
if [ "${before:-0}" -lt 1 ]; then FROM="the"; TO="THE_X"; before=$(count "$FROM" "$SRC"); fi
echo "==> will globally replace '$FROM' ($before occurrences) with '$TO'"

tmux kill-session -t "$SESSION" 2>/dev/null
tmux new-session -d -s "$SESSION" -x 200 -y 50 \
    "cd '$WORK' && HOME='$HOME_ISO' PYTHONPATH='$REPO' $PY -m drydock --model gemma4 --provider vllm --base-url http://localhost:8000/v1"
sleep 10

send() { tmux send-keys -t "$SESSION" "$1"; sleep 0.5; tmux send-keys -t "$SESSION" Enter; sleep "${2:-5}"; }
send "/doc open $NAME" 6
send "/doc search $NAME $FROM" 5
send "/doc replace $NAME $FROM :: $TO" 6
send "/doc commit $NAME" 6

echo "==> TUI transcript (tail):"
tmux capture-pane -t "$SESSION" -p | grep -viE '^\s*$|▔|╭|╰|│' | tail -8
tmux kill-session -t "$SESSION" 2>/dev/null

after=$(count "$FROM" "$SRC")
replaced=$(count "$TO" "$SRC")
orig_ok=$(count "$FROM" "$SRC.orig")
echo "==> RESULT: '$FROM' remaining=$after  '$TO' present=$replaced  .orig-retains-original=$orig_ok (expected $before)"
if [ "$after" -eq 0 ] && [ "$replaced" -eq "$before" ] && [ "$orig_ok" -eq "$before" ]; then
    echo "✅ PASS — large file edited through the TUI; original preserved."; rm -rf "$WORK"; exit 0
else
    echo "❌ FAIL — see $WORK"; exit 1
fi
