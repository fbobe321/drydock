#!/usr/bin/env bash
# selfdistill_digest.sh — ONE-command status of the week-long self-distillation run.
set -u
SD=/data3/tbench_local/frontier/selfdistill
echo "================= SELF-DISTILL DIGEST  $(date '+%F %T') ================="
echo "-- liveness --"
if flock -n "$SD/loop.lock" true 2>/dev/null; then echo "  LOOP: DOWN (watchdog should relaunch within 15min)"; else echo "  LOOP: alive"; fi
echo "  server: $(curl -s -m5 http://localhost:8000/v1/models 2>/dev/null | grep -q gemma4 && echo up || echo DOWN)"
echo "  champion: $(cat "$SD/champion.txt" 2>/dev/null)"
echo "  collectors: $(pgrep -fc frontier_collect_n3.sh 2>/dev/null || echo 0) stream(s)"
echo "  heartbeat: $(cat "$SD/heartbeat.txt" 2>/dev/null)"
echo
echo "-- corpus --"
echo "  trainable trajectories: $(find "$SD/corpus/raw" -name '*.json' 2>/dev/null | wc -l)"
echo "  held-out (never trained): $(tr '\n' ' ' < "$SD/heldout.txt" 2>/dev/null)"
echo
echo "-- generations / eval (gen,adapter,heldout_solves/total,regr_pass/total,promoted) --"
if [ -f "$SD/eval_results.csv" ]; then column -t -s, "$SD/eval_results.csv" | sed 's/^/  /'; else echo "  (no generation trained yet)"; fi
echo
echo "-- recent log --"
tail -12 "$SD/selfdistill.log" 2>/dev/null | sed 's/^/  /'
echo "========================================================================"
