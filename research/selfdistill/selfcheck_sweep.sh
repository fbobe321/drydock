#!/usr/bin/env bash
# selfcheck_sweep.sh — drive the self-authored-scorecard probe over a curated task set
# and print the confusion matrix. Operationalizes the "12 tasks queued behind the
# baseline" plan (RESUME 2026-08-23 item ⑤).
#
#   selfcheck_sweep.sh [STREAMS]        (default STREAMS=2)
#
# WHY: selfcheck_probe.sh measures ONE task — did the agent's own check agree with the
# official checker? The headline is the FALSE-PASS rate (self says pass, official says
# fail) = exactly how far a self-authored target can be trusted as a SEARCH objective on
# everyday (no-benchmark) work. This sweep runs the probe over a balanced set so the
# confusion matrix has both classes and reports it.
#
# THE TASK SET is deliberately spread across ground-truth outcomes (measured by the
# baseline sweep), because a false-pass can only occur where the TRUE answer is FAIL:
#   - 4 TRUE-PASS   (agent solves it single-attempt)  -> measures agreement + false-fail
#   - 4 NEAR-MISS   (partial, official FAIL)           -> the subtle false-pass band
#   - 4 FLATLINE    (0/N, official FAIL)               -> the blatant false-pass band
#
# QUEUED BEHIND THE BASELINE: both drive the same single .20 server, so this WAITS for
# baseline_sweep.sh to exit before it starts — otherwise the two would contend for the
# GPU and corrupt each other's single-attempt timings. It is also listed in
# fleet_supervisor.sh::experiment_active so the supervisor will not reclaim the freed .20
# workers for the campaign while this runs.
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/selfcheck"; mkdir -p "$OUT"
LOG="$OUT/sweep.log"
STREAMS="${1:-2}"
export LLM_URL="${LLM_URL:-http://192.168.50.20:8000/v1}"
export DD_VER="${DD_VER:-3.1.15}"
BUDGET="${BUDGET:-900}"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# balanced 12: 4 true-pass, 4 near-miss (partial, official FAIL), 4 flatline (0/N, FAIL)
TASKS=(
  fix-git openssl-selfsigned-cert nginx-request-logging headless-terminal
  cancel-async-tasks llm-inference-batching-scheduler path-tracing fix-code-vulnerability
  adaptive-rejection-sampler db-wal-recovery constraints-scheduling crack-7z-hash
)

# resumable: a task is done if it already has a row in the results CSV
CSV="$OUT/selfcheck_results.csv"
_done(){ [ -f "$CSV" ] && awk -F, -v t="$1" '$1==t{f=1} END{exit f?0:1}' "$CSV" 2>/dev/null; }

# ── wait for the baseline to drain (shared .20 server) ───────────────────────
if pgrep -f 'baseline_sweep.sh' >/dev/null 2>&1; then
  say "baseline_sweep still running — waiting for it to drain before starting (shared .20 server)"
  while pgrep -f 'baseline_sweep.sh' >/dev/null 2>&1; do sleep 120; done
  say "baseline drained; starting selfcheck sweep"
fi

run_one(){   # run_one <task>
  local t="$1"
  _done "$t" && { say "SKIP $t (already measured)"; return 0; }
  # collision guard: the campaign lane may own this container
  docker ps -a --format '{{.Names}}' | grep -qx "ddt_$t" && { say "SKIP $t (container busy)"; return 0; }
  say "PROBE $t"
  LLM_URL="$LLM_URL" DD_VER="$DD_VER" bash "$SD/selfcheck_probe.sh" "$t" "$BUDGET" >>"$LOG" 2>&1
}

say "SELFCHECK sweep: ${#TASKS[@]} tasks over $STREAMS streams (server $LLM_URL)"
stream(){   # stream <idx> — take every STREAMS-th task
  local idx="$1" i=0 t
  for t in "${TASKS[@]}"; do
    if [ $(( i % STREAMS )) -eq "$idx" ]; then run_one "$t"; fi
    i=$((i+1))
  done
}
for s in $(seq 0 $((STREAMS-1))); do stream "$s" & done
wait
say "SELFCHECK DONE -> $CSV"
"$SD/selfcheck_report.py" 2>&1 | tee -a "$LOG" || true
