#!/usr/bin/env bash
# tg_notify_ratchet.sh — wait for the ratchet run, Telegram the result. Detached, plain text.
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/ratchet/ratchet_results.csv"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"
for i in $(seq 1 1200); do pgrep -f 'sd_train_oneshot_ratchet_run' >/dev/null 2>&1 || break; sleep 30; done
sleep 5
/home/bobef/miniconda3/bin/python3 - "$OUT" "$TOKEN" "$CHAT" <<'PY'
import sys, urllib.request, urllib.parse, json, csv, subprocess
out, token, chat = sys.argv[1:4]
rows=[]
try:
    for r in csv.DictReader(open(out)):
        if r.get("task"): rows.append(r)
except Exception: pass
solved=[r["task"] for r in rows if r.get("solved")=="1"]
lines=[f"{r['task']}: solved={r.get('solved')} best={r.get('best')}/{r.get('total')}" for r in rows]
loop=subprocess.run("pgrep -f 'flock.*loop.lock'",shell=True,capture_output=True).returncode==0
if not rows: v="no results — check ratchet_run.log"
elif solved: v=(f"🎉 RATCHET CRACKED {len(solved)}/{len(rows)}: {', '.join(solved)}. Cumulative-selection SOLVED "
                f"task(s) the base can't one-shot -> the volume-ceiling breaker WORKS. Next: integrate the "
                f"ratchet into collection to grow the corpus past 7.")
else:
    climbed=sum(1 for r in rows if int(r.get('best',0) or 0)>0)
    v=(f"No full solves, but {climbed}/{len(rows)} showed partial climb (best>0). The ratchet is making "
       f"graded progress; may need more rounds / budget, or these hosts lack a gradient. Review the per-round curves.")
msg=("drydock self-distill — RATCHET test DONE (persist verified progress across rounds; real-checker fitness)\n\n"
     + "\n".join(lines) + f"\n\n{v}\n\nLoop relaunched: {'yes' if loop else 'NOT yet'}. ratchet/ logs + ratchet_results.csv")
data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
try: r=urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",data,timeout=20); print("TG_SENT", json.load(r).get("ok"))
except Exception as e: print("TG_FAILED", e)
PY
