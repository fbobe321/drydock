#!/usr/bin/env bash
# moe_orchestrate.sh — sequence the MoE experiment without disrupting live work.
#
# Waits for BOTH: (a) the .20 lanes' in-flight flatline jobs to finish (so nothing
# is lost and the auto-park records fresh 2.1 evidence), and (b) the MoE collect to
# finish growing the corpus. THEN fires moe_train_eval.sh, which frees .20 and trains.
set -u
SD=/data3/tbench_local/frontier/selfdistill
CL="$SD/cluster"
LOG="$SD/moe_collect/orchestrate.log"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "waiting for .20 lanes to drain + MoE collect to finish"
for i in $(seq 1 960); do            # up to ~8h at 30s
  running20=$(awk -F'\t' '$6=="running" && ($7=="20a"||$7=="20b")' "$CL/queue.tsv" 2>/dev/null | wc -l)
  collect_done=0
  grep -q 'COLLECT2 ALL DONE' "$SD/moe_collect/collect.log" 2>/dev/null && collect_done=1
  n_traj=$(ls "$SD"/moe_collect/*_solved_traj.json 2>/dev/null | wc -l)
  if [ "$((i % 20))" -eq 1 ]; then
    say "  .20 jobs running=$running20 · collect_done=$collect_done · corpus=$n_traj"
  fi
  if [ "$running20" -eq 0 ] && [ "$collect_done" -eq 1 ]; then
    say "READY: .20 lanes drained, collect finished, corpus=$n_traj"
    break
  fi
  sleep 30
done

n_traj=$(ls "$SD"/moe_collect/*_solved_traj.json 2>/dev/null | wc -l)
say "parked flatlines now: $(wc -l < "$SD/heredity/parked_flatlines.txt" 2>/dev/null || echo 0)"
say "firing moe_train_eval.sh (corpus=$n_traj)"
bash "$SD/moe_train_eval.sh"
rc=$?
say "moe_train_eval.sh exited rc=$rc"
exit $rc
