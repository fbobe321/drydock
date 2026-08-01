#!/usr/bin/env bash
# tg_notify_condensed.sh — wait for the condensed-distillation test, Telegram the verdict. Detached.
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/condensed_results.csv"; LOG="$SD/condensed.log"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"
for i in $(seq 1 480); do pgrep -f 'sd_train_oneshot_condensed' >/dev/null 2>&1 || break; sleep 30; done
/home/bobef/miniconda3/bin/python3 - "$OUT" "$LOG" "$TOKEN" "$CHAT" <<'PY'
import sys, urllib.request, urllib.parse, json, subprocess, re
out, log, token, chat = sys.argv[1:5]
rows=[]
try:
    for l in open(out):
        l=l.strip()
        if not l or l.startswith("config"): continue
        rows.append(l.split(","))
except Exception: pass
solves=sum(1 for r in rows if r and r[-1]=="1"); total=len(rows)
mll=""
try:
    for line in open(log):
        m=re.search(r"mean_last_10': ([0-9.]+)", line)
        if m: mll=m.group(1)
except Exception: pass
loop_up = subprocess.run("pgrep -f 'flock.*loop.lock'", shell=True, capture_output=True).returncode==0
if total==0:
    verdict="⚠️ no result rows — check condensed.log"
elif solves>0:
    verdict=(f"✅✅ CONDENSED REPRODUCES — {solves}/{total} solves on break-filter, where RAW+LoRA=0 and plain=0. "
             f"raw✗ → condensed✓ on the SAME task. THE FIX: the loop must CONDENSE traces (task→winning file "
             f"edits→verify) before training, not train raw trajectories. Operator hypothesis HOLDS in condensed form.")
else:
    verdict=(f"❌ CONDENSED also INERT — 0/{total} despite loss→~0 ({mll}). Even condensed memorization on the "
             f"matched base doesn't reproduce → the block is deeper than trajectory format; redirect.")
detail=" | ".join("r%s=%s"%(r[2] if len(r)>2 else "?", r[-1]) for r in rows) if rows else ""
msg=(f"🧪 *drydock self-distill — CONDENSED distillation test DONE*\n\n"
     f"Task: break-filter-js-from-html (condensed solve → loss→0, r=32) on matched HF-Q5 base, real TUI ×{total}\n"
     f"Runs: {detail}\n\n{verdict}\n\nLoop relaunched: {'yes ✓' if loop_up else 'NOT yet'}\nResults: {out}")
url=f"https://api.telegram.org/bot{token}/sendMessage"
data=urllib.parse.urlencode({"chat_id":chat,"text":msg,"parse_mode":"Markdown"}).encode()
try:
    r=urllib.request.urlopen(url,data,timeout=20); print("TG_SENT", json.load(r).get("ok"))
except Exception as e:
    print("TG_FAILED", e)
PY
