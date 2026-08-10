#!/usr/bin/env bash
# lib.sh — shared state + atomic queue ops for the 3-box task-parallel cluster.
#
# WHY this shape: the boxes are WiFi-linked (~112 Mbps) with SEPARATE local /data3, so
# tightly-coupled distributed compute is out. This is a task-parallel cluster: ONE queue on
# the coordinator (.22), workers drain it, coordination state is tiny (job rows + result rows).
# All mutations go through flock on QLOCK so concurrent workers never corrupt the queue.
#
# Queue row (TAB-separated): jobid  type  task  rounds  budget  status  worker  claimed_ts  done_ts  result
#   type   : escalate | collect | train | eval   (extensible)
#   status : pending | running | done | failed
CL_DIR="${CL_DIR:-/data3/tbench_local/frontier/selfdistill/cluster}"
QUEUE="$CL_DIR/queue.tsv"
QLOCK="$CL_DIR/queue.lock"
RESULTS="$CL_DIR/results.tsv"
mkdir -p "$CL_DIR"
[ -f "$QUEUE" ]   || printf 'jobid\ttype\ttask\trounds\tbudget\tstatus\tworker\tclaimed_ts\tdone_ts\tresult\n' > "$QUEUE"
[ -f "$RESULTS" ] || printf 'ts\tworker\tjobid\ttype\ttask\tstatus\tresult\n' > "$RESULTS"

# _now — timestamp (Date.now() unavailable in some contexts; date is fine in bash)
_now(){ date +%s; }

# q_enqueue <type> <task> [rounds] [budget]  -> prints jobid
q_enqueue(){
  local type="$1" task="$2" rounds="${3:-10}" budget="${4:-1200}"
  exec 9>"$QLOCK"; flock 9
  local jid; jid="$(_now)_$$_$(awk 'END{print NR}' "$QUEUE")"
  printf '%s\t%s\t%s\t%s\t%s\tpending\t-\t-\t-\t-\n' "$jid" "$type" "$task" "$rounds" "$budget" >> "$QUEUE"
  flock -u 9
  echo "$jid"
}

# q_claim <worker>  -> prints "jobid\ttype\ttask\trounds\tbudget" of the claimed job, or nothing
# Skips a task whose ddt_<task> container already exists (another worker is on it).
q_claim(){
  local worker="$1" line jid type task
  exec 9>"$QLOCK"; flock 9
  while IFS=$'\t' read -r jid type task rounds budget status _; do
    [ "$status" = "pending" ] || continue
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "ddt_$task" && continue
    # claim it
    local tmp; tmp="$(mktemp)"
    awk -F'\t' -v OFS='\t' -v j="$jid" -v w="$worker" -v ts="$(_now)" \
      '$1==j{$6="running";$7=w;$8=ts} {print}' "$QUEUE" > "$tmp" && mv "$tmp" "$QUEUE"
    flock -u 9
    printf '%s\t%s\t%s\t%s\t%s\n' "$jid" "$type" "$task" "$rounds" "$budget"
    return 0
  done < <(tail -n +2 "$QUEUE")
  flock -u 9
  return 1
}

# q_complete <jobid> <status> <result>
q_complete(){
  local jid="$1" status="$2" result="$3" worker type task
  exec 9>"$QLOCK"; flock 9
  read -r worker type task < <(awk -F'\t' -v j="$jid" '$1==j{print $7,$2,$3}' "$QUEUE")
  local tmp; tmp="$(mktemp)"
  awk -F'\t' -v OFS='\t' -v j="$jid" -v st="$status" -v ts="$(_now)" -v r="$result" \
    '$1==j{$6=st;$9=ts;$10=r} {print}' "$QUEUE" > "$tmp" && mv "$tmp" "$QUEUE"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(_now)" "$worker" "$jid" "$type" "$task" "$status" "$result" >> "$RESULTS"
  flock -u 9
}
