#!/usr/bin/env bash
# worker.sh <worker_id> <server_url> [dd_ver]  — a cluster worker.
#
# Loops: claim next pending job from the shared queue → run it locally (ddt containers on THIS
# harness host, driving the assigned <server_url>) → write the result back. Idle-polls when the
# queue is drained; exits only on `touch cluster/STOP` or SIGTERM (pkill -f "worker.sh <id>").
#
# NOTE (Phase 1): ddt containers still run on the .22 harness host regardless of which server the
# job drives (only .22 has the harness today). Phase 2 = deploy the harness to .20/.21 so workers
# run ddt LOCALLY on each box (uses each box's own CPUs, removes the .22 CPU cap). See README.
set -u
WID="${1:?usage: worker.sh <worker_id> <server_url> [dd_ver]}"
SERVER="${2:?need server_url e.g. http://192.168.50.20:8000/v1}"
DDVER="${3:-3.0.138}"
SD=/data3/tbench_local/frontier/selfdistill
CL_DIR="$SD/cluster"; source "$CL_DIR/lib.sh"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
export FINE_FITNESS="${FINE_FITNESS:-1}" ABORT_FLAT_ROUNDS="${ABORT_FLAT_ROUNDS:-4}"
LOG="$CL_DIR/worker_$WID.log"
HB="$CL_DIR/worker_$WID.hb"
say(){ echo "[$(date '+%F %T')] [$WID] $*" | tee -a "$LOG"; }

say "worker up → server=$SERVER dd_ver=$DDVER"
idle=0
while true; do
  [ -f "$CL_DIR/STOP" ] && { say "STOP flag → exiting"; exit 0; }
  # health-gate the assigned server (don't score a dead server as failure)
  if ! curl -sf -m8 "$SERVER/models" 2>/dev/null | grep -q gemma4; then
    say "server $SERVER not ready; waiting"; sleep 20; continue
  fi
  job="$(q_claim "$WID")" || job=""
  if [ -z "$job" ]; then
    idle=$((idle+1)); echo "[$(date '+%F %T')] idle ($idle) — queue drained" > "$HB"
    [ $((idle % 10)) -eq 1 ] && say "queue drained; idle-polling"
    sleep 30; continue
  fi
  idle=0
  IFS=$'\t' read -r jid type task rounds budget <<<"$job"
  echo "[$(date '+%F %T')] running $type/$task (job $jid) on $SERVER" > "$HB"
  say "claim job=$jid type=$type task=$task rounds=$rounds budget=$budget"
  case "$type" in
    escalate|collect)
      LLM_URL="$SERVER" DD_VER="$DDVER" bash "$SD/ratchet_solve.sh" "$task" "$rounds" "$budget" >>"$LOG" 2>&1
      # ratchet_solve appends this task's row to the shared ratchet_results.csv
      res="$(awk -F, -v t="$task" '$1==t{r=$0} END{print r}' "$SD/ratchet/ratchet_results.csv" 2>/dev/null)"
      solved="$(echo "$res" | awk -F, '{print $2}')"
      q_complete "$jid" "$([ "$solved" = "1" ] && echo done || echo failed)" "${res:-no-row}"
      say "done job=$jid → ${res:-no-row}"
      ;;
    *)
      q_complete "$jid" failed "unknown-type:$type"
      say "unknown job type $type (job $jid) — marked failed"
      ;;
  esac
done
