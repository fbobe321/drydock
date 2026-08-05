#!/usr/bin/env bash
# tg_notify_ratchet_hard.sh — wait for the HARD ratchet sweep to finish, Telegram the result. Detached, plain text.
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/ratchet/ratchet_hard_results.csv"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"
# block until the sweep process is gone (up to ~10h at 30s/tick)
for i in $(seq 1 1200); do pgrep -f 'sd_train_oneshot_ratchet_hard' >/dev/null 2>&1 || break; sleep 30; done
sleep 5
/home/bobef/miniconda3/bin/python3 - "$OUT" "$TOKEN" "$CHAT" <<'PY'
import sys, urllib.request, urllib.parse, json, csv
out, token, chat = sys.argv[1:4]
rows=[]
try:
    for r in csv.DictReader(open(out)):
        if r.get("task"): rows.append(r)
except Exception: pass
solved=[r["task"] for r in rows if r.get("solved")=="1"]
climbed=[r for r in rows if (int(r.get('best',0) or 0)>0 and r.get('solved')!='1')]
lines=[f"{r['task']}: solved={r.get('solved')} best={r.get('best')}/{r.get('total')}" for r in rows]
if not rows: v="no results — check ratchet_hard_run.log"
elif solved: v=(f"🎉 RATCHET CRACKED {len(solved)}/{len(rows)} HARD task(s): {', '.join(solved)}. Cumulative "
                f"selection solved task(s) the base can't one-shot on genuinely hard substrate.")
elif climbed: v=(f"No full solves, but {len(climbed)}/{len(rows)} climbed above 0 across rounds "
                 f"({', '.join(r['task'] for r in climbed)}) — the pawl grabbed a partial-credit gradient. Review per-round curves.")
else: v=("All tasks stayed at 0 across rounds — no partial-credit gradient for the ratchet to grab on these. "
         "Try tasks with more independent sub-checks, or more rounds/budget.")
msg=("drydock self-distill — HARD RATCHET SWEEP DONE (persist verified progress across rounds; real-checker fitness)\n\n"
     + "\n".join(lines) + f"\n\n{v}\n\nNormal loop was intentionally left DOWN. Logs: ratchet_hard_run.log + ratchet/*.log")
data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
try: r=urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",data,timeout=20); print("TG_SENT", json.load(r).get("ok"))
except Exception as e: print("TG_FAILED", e)
PY
touch "$SD/ratchet_hard_notify.done"
