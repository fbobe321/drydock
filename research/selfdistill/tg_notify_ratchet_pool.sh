#!/usr/bin/env bash
# tg_notify_ratchet_pool.sh — wait for the pool sweep's DONE file, Telegram the tally. Detached.
# Waits on the done-file (NOT pgrep) to avoid matching its own command line.
set -u
SD=/data3/tbench_local/frontier/selfdistill
DONE="$SD/ratchet_pool.done"; MASTER="$SD/ratchet/ratchet_pool_results.csv"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"
for i in $(seq 1 20000); do [ -f "$DONE" ] && break; sleep 60; done
/home/bobef/miniconda3/bin/python3 - "$MASTER" "$TOKEN" "$CHAT" <<'PY'
import sys, urllib.request, urllib.parse, json, csv
master, token, chat = sys.argv[1:4]
rows=[r for r in csv.DictReader(open(master)) if r.get("task")]
solved=[r["task"] for r in rows if r.get("solved")=="1"]
climb=[r for r in rows if r.get("solved")=="0" and (r.get("best") or "0").isdigit() and int(r["best"])>0]
msg=(f"drydock RATCHET POOL SWEEP DONE — {len(solved)}/{len(rows)} tbench tasks cracked by cumulative "
     f"selection (base model, no teacher).\n\nSOLVED: {', '.join(solved) if solved else '(none)'}\n"
     f"Partial climb (best>0, not solved): {len(climb)}\n\nHow many NEW tbench tasks the ratchet passes = "
     f"{len(solved)}. Next: train a self-distill LoRA on these solved trajectories + re-measure transfer.")
data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
try: r=urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",data,timeout=20); print("TG_SENT", json.load(r).get("ok"))
except Exception as e: print("TG_FAILED", e)
PY
