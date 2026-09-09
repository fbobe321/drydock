#!/usr/bin/env bash
# run_loop_control.sh — execute the PRE-REGISTERED ledger-vs-plain-ratchet control.
#
# Runs both arms of loop_control/PRE_REGISTRATION.md on the same tasks/budget on the
# freed .20 lane, PAIRED per task (plain first, then with-loop), same engine
# (ratchet_solve.sh), one variable = whether the first-principles Ledger tool is pinned.
#
# SAFE BY CONSTRUCTION — no session is killed and no in-flight job is orphaned:
#   · this script's name is in fleet_supervisor.sh::experiment_active(), so while it runs
#     the supervisor SKIPS all refill + keepalive (no new jobs enqueued, .20 stays free);
#   · it WAITS for the .20 workers to finish their current jobs and go idle before taking
#     the lane — the 2026-08-21 disaster was killing drivers mid-job; this does the opposite;
#   · it drives ddt_<task> directly and ABORTS on any container the fleet still owns.
# On exit the supervisor auto-resumes the campaign on the next tick.
set -u
SD=/data3/tbench_local/frontier/selfdistill
ROOT=/data3/tbench_local
export TASKS="$ROOT/tasks/terminal-bench-2"
export LLM_URL="${LLM_URL:-http://192.168.50.20:8000/v1}"
# The Ledger tool (groundtruth + bottleneck, PRD §19) landed AFTER PyPI v3.1.25 was
# published without a version bump, so `drydock-cli==3.1.25` on PyPI has NO Ledger tool
# — the 2026-09-08 pilot pinned a tool that did not exist and both arms ran identical
# plain ratchets (see PRE_REGISTRATION.md "Pilot #1 — VOID"). Deploy the local wheel that
# actually contains the tool to BOTH arms via DD_WHEEL, so the only variable stays the
# pin. DD_WHEEL takes precedence over DD_VER in tui_task_lib.sh; the running fleet keeps
# its own PyPI pin and is untouched.
export DD_VER="${DD_VER:-3.1.26}"
export DD_WHEEL="${DD_WHEEL:-$SD/loop_control/drydock_cli-3.1.26-py3-none-any.whl}"
if [ ! -f "$DD_WHEEL" ]; then
  echo "FATAL: DD_WHEEL not found at $DD_WHEEL — build it: (cd /data3/drydock-v3 && python3 -m build --wheel)"; exit 1
fi
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
source "$ROOT/tui_task_lib.sh"
PY=/home/bobef/miniconda3/bin/python3
OUT="$SD/loop_control"; mkdir -p "$OUT"
MAX_ROUNDS="${1:-6}"; ROUND_BUDGET="${2:-900}"
TASKFILE="${3:-$OUT/tasks.txt}"
CSV="$OUT/results.csv"
LOG="$OUT/run.log"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
[ -f "$CSV" ] || echo "task,arm,solved,best,total,ledger_used" > "$CSV"

# ── wait for the .20 lane to go idle (workers not 'running' a job) ────────────
wait_for_idle(){
  local waited=0 max=10800   # up to 3h; the 3 in-flight jobs are bounded by their budgets
  while [ "$waited" -lt "$max" ]; do
    local a b
    a=$(cat "$SD/cluster/worker_20a.hb" 2>/dev/null)
    b=$(cat "$SD/cluster/worker_20b.hb" 2>/dev/null)
    if ! echo "$a" | grep -q "running " && ! echo "$b" | grep -q "running "; then
      say ".20 lanes idle — claiming the lane"
      return 0
    fi
    [ $((waited % 300)) -eq 0 ] && say "waiting for .20 to drain (20a/20b still running a job)…"
    sleep 30; waited=$((waited+30))
  done
  say "WARN: .20 did not go idle within ${max}s — proceeding anyway with the abort-if-busy guard"
  return 0
}

# ── EXPOSURE PREFLIGHT — the real wiring guard (replaces the broken trajectory metric) ──
# Pilot #1 was VOID because the Ledger tool was never OFFERED to the model, yet nothing
# caught it: the trajectory `tools` field records only tools the model CALLED, so absence
# there cannot distinguish "not offered" from "offered-but-unused". This preflight settles
# offering ONCE, up front, against the actual deployed wheel: bring a container up via the
# same ddt_up path (installs DD_WHEEL), write the with-loop config (pin_tools=["Ledger"]),
# and run drydock's real schemas()→select_tools(pin)→filter_tool_schemas(gemma4) pipeline
# in the container. If Ledger is not in the final tool list, ABORT the whole experiment —
# a null result would be meaningless. No LLM calls, no .20 lane needed.
preflight_exposure(){   # <vehicle-task>
  local task="$1" ctr="ddt_$1"
  if docker ps -a --format '{{.Names}}' | grep -qx "$ctr"; then
    say "PREFLIGHT ABORT: $ctr exists (fleet owns it) — cannot verify exposure safely"; return 1
  fi
  say "PREFLIGHT: verifying the Ledger tool is OFFERED to gemma4 under $(basename "$DD_WHEEL") + pin_tools=[\"Ledger\"] (vehicle=$task)…"
  ddt_up "$task" >>"$LOG" 2>&1 || { say "PREFLIGHT: ddt_up failed"; return 1; }
  printf 'trajectory_file = "/app/.dd_trajectory.json"\npin_tools = ["Ledger"]\n' \
    | docker exec -i "$ctr" bash -c 'mkdir -p ~/.drydock && cat > ~/.drydock/config.toml' 2>/dev/null
  local out
  out=$(docker exec "$ctr" bash -lc 'PY=$HOME/.local/share/uv/tools/drydock-cli/bin/python; "$PY" - <<"EOF"
import drydock, drydock.config as c, drydock.tools
from drydock.tool_registry import schemas
from drydock.tool_select import select_tools, DEFAULT_MAX_TOOLS
from drydock.tuning import filter_tool_schemas
cfg=c.resolve({}, c.default_config_path())
pins=list(cfg.get("pin_tools") or [])
kept=select_tools(schemas(), phase="execute", task_text="", max_tools=cfg.get("max_tools", DEFAULT_MAX_TOOLS), pin_tools=pins)
final=[s.get("name") for s in filter_tool_schemas(kept, "gemma4")]
print("VER", drydock.__version__, "PIN", pins, "LEDGER_OFFERED", "Ledger" in final)
EOF' 2>/dev/null)
  say "PREFLIGHT: $out"
  ddt_down "$task" >/dev/null 2>&1
  echo "$out" | grep -q "LEDGER_OFFERED True" && return 0
  say "PREFLIGHT FAILED: Ledger is NOT offered — refusing to run (a null here would be meaningless, as in pilot #1)."
  return 1
}

# ── run one arm of one task; append a tagged result row ──────────────────────
run_arm(){   # <task> <arm: plain|loop>
  local task="$1" arm="$2" ctr="ddt_$1" pins="" used="n/a"
  if docker ps -a --format '{{.Names}}' | grep -qx "$ctr"; then
    say "ABORT $task/$arm: $ctr exists (fleet owns it) — skipping"; return 0
  fi
  [ "$arm" = "loop" ] && pins='"Ledger"'
  say "RUN $task arm=$arm (LOOP_PINS=[${pins}], rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s, wheel=$(basename "$DD_WHEEL"))"
  LOOP_PINS="$pins" LLM_URL="$LLM_URL" DD_VER="$DD_VER" DD_WHEEL="$DD_WHEEL" \
    bash "$SD/ratchet_solve.sh" "$task" "$MAX_ROUNDS" "$ROUND_BUDGET" >>"$LOG" 2>&1
  # p2 USAGE signal from the HOST-SIDE trajectory captures (survives teardown): reports
  # calls=<Ledger invocations>;used_rounds=<rounds with >=1 call>/<rounds>. This measures
  # whether the model CHOSE to use the tool. It does NOT measure exposure — the trajectory
  # only records tools that were CALLED — so the wiring guard is the up-front
  # preflight_exposure(), not this. Exposure is already established when we reach here.
  if [ "$arm" = "loop" ]; then
    used=$("$PY" "$OUT/ledger_usage.py" "$SD/ratchet/capture/$task" 2>/dev/null)
    [ -z "$used" ] && used="unmeasured"
  fi
  local row; row=$(awk -F, -v t="$task" '$1==t{r=$0} END{print r}' "$SD/ratchet/ratchet_results.csv" 2>/dev/null)
  local solved best total
  solved=$(echo "$row" | awk -F, '{print $2}'); best=$(echo "$row" | awk -F, '{print $3}'); total=$(echo "$row" | awk -F, '{print $4}')
  echo "$task,$arm,${solved:-ERR},${best:-},${total:-},${used}" >> "$CSV"
  say "RESULT $task/$arm solved=${solved:-ERR} ${best:-?}/${total:-?} ledger=${used}"
  ddt_down "$task" >/dev/null 2>&1
}

say "===== LOOP CONTROL START (rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s) ====="
say "tasks: $(tr '\n' ' ' < "$TASKFILE")"
# WIRING GUARD FIRST — prove the Ledger is offered before spending hours on arms whose
# null would be meaningless (the pilot-#1 trap). Vehicle = first task in the list.
PREFLIGHT_TASK=$(grep -m1 -v '^[[:space:]]*$' "$TASKFILE")
if ! preflight_exposure "$PREFLIGHT_TASK"; then
  say "===== ABORTED at preflight — fix the wheel/pin so the Ledger is offered, then relaunch ====="
  exit 1
fi
wait_for_idle
while read -r task; do
  [ -z "$task" ] && continue
  run_arm "$task" plain
  run_arm "$task" loop
done < "$TASKFILE"
say "===== LOOP CONTROL DONE — see $CSV ====="
column -t -s, "$CSV" 2>/dev/null | tee -a "$LOG"
