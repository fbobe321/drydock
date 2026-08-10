#!/usr/bin/env bash
# enqueue.sh — add jobs to the cluster queue.
#   enqueue.sh job <type> <task> [rounds] [budget]     # one job
#   enqueue.sh escalation [threshold_frac]             # seed all unsolved near-miss partials
# Escalation seeding mirrors ratchet_20_sweep's ranking (closest-first), excluding solved-anywhere.
set -u
SD=/data3/tbench_local/frontier/selfdistill
source "$SD/cluster/lib.sh"
cmd="${1:-}"; shift || true
case "$cmd" in
  job)
    jid="$(q_enqueue "$1" "$2" "${3:-10}" "${4:-1200}")"; echo "enqueued $jid: $1/$2" ;;
  escalation)
    SHARED="$SD/ratchet/ratchet_results.csv"; R21="$SD/ratchet21/ratchet21_results.csv"; R22="$SD/ratchet22/ratchet22_results.csv"; R20="$SD/ratchet20/ratchet20_results.csv"
    solved_any="$(mktemp)"
    awk -F, 'NR>1 && $2==1{print $1}' "$SHARED" "$SD/ratchet/ratchet_pool_results.csv" "$R21" "$R22" "$R20" 2>/dev/null | sed '/^$/d' | sort -u > "$solved_any"
    cat "$SD/base_solved.txt" "$SD/heldout.txt" "$SD/regression.txt" 2>/dev/null | sed '/^$/d' | sort -u >> "$solved_any"
    # already queued (any status) — don't double-enqueue
    queued="$(mktemp)"; awk -F'\t' 'NR>1{print $3}' "$QUEUE" | sort -u > "$queued"
    n=0
    while IFS= read -r t; do
      grep -qxF "$t" "$solved_any" && continue
      grep -qxF "$t" "$queued" && continue
      q_enqueue escalate "$t" 10 1200 >/dev/null; n=$((n+1))
    done < <(awk -F, 'FNR>1 && $2==0 && $4>0 && ($3>0 || $5>0){
        frac=$3/$4; score=frac*1000 + $5/1000000;
        if(score>seen[$1]){seen[$1]=score; frac_of[$1]=frac; part_of[$1]=$5}
      } END{ for(k in seen) printf "%.6f\t%d\t%s\n", frac_of[k], part_of[k], k }' \
      "$R21" "$R22" "$SHARED" 2>/dev/null | sort -rn -k1,1 -k2,2 | awk -F'\t' '{print $3}')
    rm -f "$solved_any" "$queued"
    echo "enqueued $n escalation jobs (closest-first, unsolved partials)" ;;
  *)
    echo "usage: enqueue.sh job <type> <task> [rounds] [budget]  |  enqueue.sh escalation"; exit 1 ;;
esac
