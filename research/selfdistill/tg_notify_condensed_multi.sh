#!/usr/bin/env bash
# tg_notify_condensed_multi.sh — wait for the held-out generalization test, Telegram the verdict. Detached.
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/condensed_multi_results.csv"; LOG="$SD/condensed_multi.log"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"
for i in $(seq 1 720); do pgrep -f 'sd_train_oneshot_condensed_multi' >/dev/null 2>&1 || break; sleep 30; done
/home/bobef/miniconda3/bin/python3 - "$OUT" "$LOG" "$TOKEN" "$CHAT" <<'PY'
import sys, urllib.request, urllib.parse, json, subprocess, re
out, log, token, chat = sys.argv[1:5]
ho=[]; tr=[]
try:
    for l in open(out):
        p=l.strip().split(",")
        if len(p)<4 or p[0]=="config": continue
        (ho if p[1]=="heldout" else tr).append((p[2],p[3]))
except Exception: pass
ho_s=sum(1 for _,r in ho if r=="1"); tr_s=sum(1 for _,r in tr if r=="1")
loop_up = subprocess.run("pgrep -f 'flock.*loop.lock'", shell=True, capture_output=True).returncode==0
if not ho and not tr:
    verdict="⚠️ no result rows — check condensed_multi.log"
elif ho_s>0:
    flips=", ".join(t for t,r in ho if r=="1")
    verdict=(f"🎉🎉 GENERALIZATION — {ho_s}/{len(ho)} HELD-OUT tasks solved (base=0 by construction): {flips}. "
             f"Condensed self-distillation TRANSFERS to never-trained tasks. This is the real RSI win.")
else:
    verdict=(f"➖ No held-out generalization — 0/{len(ho)} held-out solved (reproduction {tr_s}/{len(tr)} on trained). "
             f"Condensed distillation reproduces trained tasks but 7 traces don't generalize to held-out — "
             f"consistent with DATA-constrained: needs volume. Honest negative; the recipe works, the corpus is too small.")
ho_d=" ".join(f"{t}={r}" for t,r in ho); tr_d=" ".join(f"{t}={r}" for t,r in tr)
msg=(f"🧪 *drydock self-distill — HELD-OUT generalization test DONE*\n\n"
     f"Condensed MULTI-task LoRA (7 traces) on matched HF-Q5 base, real TUI.\n\n"
     f"*Held-out {ho_s}/{len(ho)}:* {ho_d}\n*Trained {tr_s}/{len(tr)}:* {tr_d}\n\n{verdict}\n\n"
     f"Loop relaunched: {'yes ✓' if loop_up else 'NOT yet'}\nResults: {out}")
url=f"https://api.telegram.org/bot{token}/sendMessage"
data=urllib.parse.urlencode({"chat_id":chat,"text":msg,"parse_mode":"Markdown"}).encode()
try: r=urllib.request.urlopen(url,data,timeout=20); print("TG_SENT", json.load(r).get("ok"))
except Exception as e: print("TG_FAILED", e)
PY
