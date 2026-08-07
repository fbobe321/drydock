#!/usr/bin/env bash
# sd_train_oneshot_eratchet_queue.sh — QUEUE the eratchet (evolutionary ratchet) to run on the tasks
# the OLD single-incumbent ratchet couldn't crack. WAITS for the pool sweep to free the GPU (its
# done-file) so it never contends, then targets the gradient-but-stuck tasks (solved=0, best>0 —
# where fan-out + crossover should help) most-progress first. Since ratchet_evolve.sh's docker/TUI
# glue is LIVE-UNVALIDATED, the FIRST task is a SMOKE test: if the glue is broken we abort cleanly
# instead of burning hours. Named sd_train_oneshot* so the watchdog defers.
set -u
SD=/data3/tbench_local/frontier/selfdistill
POOL_DONE="$SD/ratchet_pool.done"
MASTER="$SD/ratchet/ratchet_pool_results.csv"; HARD="$SD/ratchet/ratchet_hard_results.csv"
RES="$SD/ratchet/ratchet_evolve_results.csv"
LOG="$SD/eratchet_queue.log"; DONE="$SD/eratchet_queue.done"
MAX_ROUNDS="${MAX_ROUNDS:-8}"; ROUND_BUDGET="${ROUND_BUDGET:-1200}"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
tg(){ /home/bobef/miniconda3/bin/python3 -c "
import urllib.request,urllib.parse,json,sys
try: urllib.request.urlopen('https://api.telegram.org/bot$TOKEN/sendMessage',
  urllib.parse.urlencode({'chat_id':'$CHAT','text':sys.argv[1]}).encode(),timeout=20)
except Exception as e: print('tg fail',e)" "$1" 2>/dev/null; }
server_up(){ curl -sf http://localhost:8000/health >/dev/null 2>&1; }
ensure_server(){ server_up || { say "server down — starting base"; bash "$SD/selfdistill.sh" serve base >/dev/null 2>&1 || true; sleep 8; }; }

rm -f "$DONE"
if [ "${SKIP_WAIT:-0}" != 1 ]; then
  say "eratchet queue armed — waiting for the pool sweep to finish (frees the GPU)…"
  for i in $(seq 1 30000); do [ -f "$POOL_DONE" ] && break; sleep 60; done
  say "pool sweep complete — computing gradient-stuck target list"
else
  say "SKIP_WAIT=1 — starting eratchet NOW in parallel with the remaining sweep stream"
fi
ensure_server

# gradient-but-stuck = old ratchet made partial progress but couldn't close; best-first
TASKS=$(awk -F, 'NR>1 && $2==0 && $3+0>0 {print $3" "$1}' "$MASTER" "$HARD" 2>/dev/null \
        | sort -rn | awk '{print $2}' | awk '!seen[$0]++')
n=$(echo "$TASKS" | grep -cve '^[[:space:]]*$')
say "targets ($n): $(echo "$TASKS" | tr '\n' ' ')"
[ "$n" -eq 0 ] && { say "no gradient-stuck tasks — nothing to do"; touch "$DONE"; exit 0; }
tg "eratchet queue starting on $n tasks the old ratchet couldn't crack (gradient-stuck). Smoke-testing the evolutionary driver first."

echo "task,solved,best,total" > "$RES"

# ---- SMOKE the unvalidated glue on the first (smallest-total) stuck task ----
first=$(echo "$TASKS" | awk 'NR==1')
say "=== SMOKE: eratchet on $first (validating the evolutionary docker/TUI glue) ==="
ensure_server
bash "$SD/eratchet.sh" "$first" "$MAX_ROUNDS" "$ROUND_BUDGET" 2>&1 | tail -3
if ! grep -q "EVOLVE $first DONE" "$SD/ratchet/${first}.evolve.log" 2>/dev/null; then
  say "⚠️ SMOKE FAILED — no clean DONE for $first; the evolutionary glue needs a fix. Aborting queue."
  tg "⚠️ eratchet SMOKE FAILED on $first — evolutionary driver glue is broken (see ${first}.evolve.log). Queue aborted; old-ratchet results stand."
  touch "$DONE"; exit 1
fi
say "smoke OK — evolutionary driver works; continuing on the rest"

for t in $(echo "$TASKS" | tail -n +2); do
  ensure_server
  say "--- eratchet: $t ---"
  bash "$SD/eratchet.sh" "$t" "$MAX_ROUNDS" "$ROUND_BUDGET" 2>&1 | tail -1
done

say "===== ERATCHET QUEUE DONE ====="
# old-vs-new comparison
/home/bobef/miniconda3/bin/python3 - "$RES" "$MASTER" "$HARD" <<'PY' | tee -a "$LOG"
import csv,sys
res,master,hard=sys.argv[1:4]
old={}
for m in (master,hard):
  try:
    for r in csv.DictReader(open(m)):
      if r.get("task"): old[r["task"]]=(int(r["best"] or 0),int(r["total"] or 0))
  except Exception: pass
newsolved=[]; improved=[]
for r in csv.DictReader(open(res)):
  t=r["task"]; nb=int(r["best"] or 0); nt=int(r["total"] or 0); ns=r["solved"]=="1"
  ob=old.get(t,(0,0))[0]
  if ns: newsolved.append(t)
  elif nb>ob: improved.append(f"{t} {ob}->{nb}/{nt}")
print(f"eratchet cracked {len(newsolved)} the old ratchet couldn't: {', '.join(newsolved) or '(none)'}")
print(f"further partial climb (no full solve): {', '.join(improved) or '(none)'}")
PY
SUM=$(tail -2 "$LOG")
tg "eratchet queue DONE. $SUM"
touch "$DONE"
