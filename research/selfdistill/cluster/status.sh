#!/usr/bin/env bash
# status.sh — one-glance view of the cluster queue + workers.
set -u
SD=/data3/tbench_local/frontier/selfdistill
source "$SD/cluster/lib.sh"
echo "====== CLUSTER STATUS  $(date '+%F %T') ======"
echo "-- queue ($QUEUE) --"
awk -F'\t' 'NR>1{c[$6]++} END{for(k in c) printf "  %-8s %d\n", k, c[k]}' "$QUEUE"
echo "-- running now --"
awk -F'\t' 'NR>1 && $6=="running"{printf "  %-28s worker=%-10s task=%s\n",$3,$7,$3}' "$QUEUE" | sed 's/task=[^ ]*$//'
echo "-- last 8 results --"
tail -n 8 "$RESULTS" | awk -F'\t' 'NR>0{printf "  %-10s %-10s %-6s %-26s %s\n", strftime("%H:%M",$1), $2, $6, $5, $7}' 2>/dev/null
echo "-- workers (heartbeats) --"
for hb in "$SD"/cluster/worker_*.hb; do [ -f "$hb" ] || continue; w="$(basename "$hb" .hb | sed 's/worker_//')"; echo "  $w: $(cat "$hb")"; done
echo "-- servers --"
for s in "20:http://192.168.50.20:8000" "21:http://192.168.50.21:8000" "22:http://localhost:8000"; do
  id="${s%%:*}"; url="${s#*:}"
  curl -sf -m5 "$url/v1/models" 2>/dev/null | grep -q gemma4 && echo "  .$id: UP" || echo "  .$id: down"
done
