#!/usr/bin/env bash
# eratchet ± --share, FAITHFUL to terminal-bench-2 (runs INSIDE ddt containers).
#
# The pre-registered blackboard control on the real substrate: for each tbench task, run the
# SAME `drydock eratchet` twice in fresh containers — blind (baseline) vs --share (variants
# read prior generations' scored attempts) — at identical generations/fanout, and record
# solved + generations + variant count (matched compute). One variable: --share.
#
# The integration the host path couldn't do: eratchet scores each variant in its own git
# worktree, but tbench checkers score /app and run in parallel. So the eratchet --verify is a
# wrapper that, under a flock, syncs the variant's worktree into /app and runs the task's own
# test.sh — serializing only the (fast) scoring, leaving generation parallel. drydock 3.1.29+
# (has --share) is installed in the container by ddt_up.
set -u
SD=/data3/tbench_local/frontier/selfdistill
ROOT=/data3/tbench_local
export TASKS="$ROOT/tasks/terminal-bench-2"
export LLM_URL="${LLM_URL:-http://192.168.50.22:8000/v1}"
export DD_VER="${DD_VER:-3.1.29}"                 # PyPI build that ships eratchet --share
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/.local/bin"
source "$ROOT/tui_task_lib.sh"
GENERATIONS="${1:-4}"; FANOUT="${2:-4}"
TASKFILE="${3:-$(dirname "$0")/tasks.txt}"
OUT="$(dirname "$0")"; CSV="$OUT/eratchet_container_results.csv"
LOG="$OUT/eratchet_container.log"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
[ -f "$CSV" ] || echo "task,arm,solved,best,total,generations,variants" > "$CSV"

# The in-container verify eratchet runs per variant (cwd = the variant's worktree). Under a
# lock: mirror the worktree into /app, run the task's checker, print its reward so
# score_output (fitness=auto) can read it. Keeps /tests out of /app (anti-cheat + no clobber).
VERIFY='flock /tmp/erx_score.lock bash -c '\''rsync -a --delete --exclude .git ./ /app/ >/dev/null 2>&1; \
  bash /tests/test.sh >/dev/null 2>&1; cat /logs/verifier/ctrf.json 2>/dev/null'\'' '

run_arm(){   # <task> <arm: blind|share>
  local task="$1" arm="$2" ctr="ddt_$1" shareflag=""
  [ "$arm" = "share" ] && shareflag="--share"
  if docker ps -a --format '{{.Names}}' | grep -qx "$ctr"; then
    say "ABORT $task/$arm: $ctr exists (fleet owns it) — skipping"; return 0
  fi
  say "RUN $task/$arm (gens=$GENERATIONS fanout=$FANOUT $shareflag)"
  ddt_up "$task" >>"$LOG" 2>&1 || { say "$task/$arm: ddt_up failed"; return 1; }
  # slim images lack git (eratchet worktrees) + rsync (verify sync) + flock — install once.
  docker exec "$ctr" bash -lc 'command -v git >/dev/null && command -v rsync >/dev/null && \
    command -v flock >/dev/null || (apt-get update -qq && apt-get install -y -qq git rsync \
    util-linux) >/dev/null 2>&1' >>"$LOG" 2>&1
  # /app becomes a git repo so eratchet can worktree it; /tests holds the checker.
  docker exec "$ctr" bash -lc 'cd /app && git init -q && git config user.email e@e && \
    git config user.name e && git add -A && git commit -q --allow-empty -m base' >>"$LOG" 2>&1
  docker exec "$ctr" mkdir -p /tests /logs/verifier >/dev/null 2>&1
  docker cp "$TASKS/$task/tests/." "$ctr:/tests/" >/dev/null 2>&1
  local goal; goal="$(cat "$TASKS/$task/instruction.md")"
  # Drive eratchet inside the container against the shared server.
  docker exec "$ctr" bash -lc "export PATH=\$HOME/.local/bin:\$PATH; cd /app; \
    drydock eratchet $(printf %q "$goal") --verify $(printf %q "$VERIFY") \
      --provider vllm --base-url $LLM_URL --model gemma4 \
      --generations $GENERATIONS --fanout $FANOUT $shareflag" >"$OUT/${task}_${arm}.out" 2>&1
  local out="$OUT/${task}_${arm}.out"
  local solved gens variants bt best total
  grep -q "SOLVED" "$out" && solved=1 || solved=0
  bt=$(grep -oE '[0-9]+/[0-9]+' "$out" | tail -1); best="${bt%/*}"; total="${bt#*/}"
  gens=$(grep -ciE "generation" "$out" 2>/dev/null); variants=$(grep -ciE "variant" "$out" 2>/dev/null)
  echo "$task,$arm,${solved},${best:-0},${total:-0},${gens:-0},${variants:-0}" >> "$CSV"
  say "RESULT $task/$arm solved=$solved score=${best:-0}/${total:-0} gens=${gens:-0} variants=${variants:-0}"
  ddt_down "$task" >/dev/null 2>&1
}

say "===== ERATCHET CONTAINER CONTROL (gens=$GENERATIONS fanout=$FANOUT, server=$LLM_URL) ====="
while read -r task; do
  [ -z "$task" ] && continue
  run_arm "$task" blind
  run_arm "$task" share
done < "$TASKFILE"
say "===== DONE — see $CSV ====="
column -t -s, "$CSV" 2>/dev/null | tee -a "$LOG"
