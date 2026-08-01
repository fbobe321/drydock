#!/usr/bin/env bash
# tg_notify_multirun.sh — wait for the multi-run rate-pinning eval, Telegram the true per-task rate. Detached, plain text.
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/multirun_results.csv"; LOG="$SD/multirun.log"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"
for i in $(seq 1 900); do pgrep -f 'sd_train_oneshot_multirun' >/dev/null 2>&1 || break; sleep 30; done
/home/bobef/miniconda3/bin/python3 - "$OUT" "$TOKEN" "$CHAT" <<'PY'
import sys, urllib.request, urllib.parse, json, subprocess, csv, collections
out, token, chat = sys.argv[1:4]
d=collections.defaultdict(list)
try:
    for row in csv.DictReader(open(out)): d[row["task"]].append(int(row["reward"]))
except Exception: pass
lines=[f"{t}: {sum(rs)}/{len(rs)}" for t,rs in d.items()]
any_solve=sum(1 for rs in d.values() if any(rs)); total=len(d)
solid=sum(1 for rs in d.values() if sum(rs)==len(rs) and rs)
loop_up=subprocess.run("pgrep -f 'flock.*loop.lock'",shell=True,capture_output=True).returncode==0
verdict=(f"True multi-task rate: {any_solve}/{total} tasks solve at least once, {solid}/{total} solve every run. "
         + ("Reproduction is SOLID for those tasks -> composition partially works, scale that subset."
            if solid>=2 else
            "Even the 'signal' tasks are inconsistent -> multi-task reproduction is genuinely low/noisy; "
            "generalization-via-volume (P2) is the only path."))
msg=("drydock self-distill — FORK 1: multi-run rate-pinning DONE (unpruned r=128 adapter, 3x per task)\n\n"
     + "\n".join(lines) + f"\n\n{verdict}\n\nLoop relaunched -> P2 collection resuming: {'yes' if loop_up else 'NOT yet'}. Results: {out}")
data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
try: r=urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",data,timeout=20); print("TG_SENT", json.load(r).get("ok"))
except Exception as e: print("TG_FAILED", e)
PY
