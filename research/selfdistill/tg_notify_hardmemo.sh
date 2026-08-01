#!/usr/bin/env bash
# tg_notify_hardmemo.sh — wait for the hard-overfit reproduction test to finish, then Telegram the verdict.
# Detached (setsid nohup) so it survives an SSH/session drop. NOT named sd_train_oneshot* on purpose
# (must not make the watchdog defer). Sends exactly one message.
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/hardmemo_results.csv"; LOG="$SD/hardmemo.log"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"

# wait up to 4h for hardmemo to exit
for i in $(seq 1 480); do
  pgrep -f 'sd_train_oneshot_hardmemo' >/dev/null 2>&1 || break
  sleep 30
done

/home/bobef/miniconda3/bin/python3 - "$OUT" "$LOG" "$TOKEN" "$CHAT" <<'PY'
import sys, urllib.request, urllib.parse, json, subprocess
out, log, token, chat = sys.argv[1:5]
rows=[]
try:
    for l in open(out):
        l=l.strip()
        if not l or l.startswith("config"): continue
        parts=l.split(","); rows.append(parts)
except Exception: pass
solves=sum(1 for r in rows if r and r[-1]=="1"); total=len(rows)
# loss
mll=""
try:
    import re
    for line in open(log):
        m=re.search(r"mean_last_10': ([0-9.]+)", line)
        if m: mll=m.group(1)
except Exception: pass
loop_up = subprocess.run("pgrep -f 'flock.*loop.lock'", shell=True, capture_output=True).returncode==0
if total==0:
    verdict="⚠️ no result rows (test may have failed to serve/eval — check hardmemo.log)"
elif solves>0:
    verdict=f"✅ REPRODUCES — {solves}/{total} solves. A loss→0 adapter on the MATCHED base DOES reproduce a trained task → 'if it can solve it, we can train it to do it' HOLDS. Fix loop (matched base + real memorization) and scale."
else:
    verdict=f"❌ INERT — 0/{total} solves despite loss→~0 ({mll}) on the matched base. Weight-space memorization does NOT manifest as agentic behavior at inference — the deep finding; redirects the approach."
detail=" | ".join("%s=%s"%(r[2] if len(r)>2 else "?", r[-1]) for r in rows) if rows else ""
msg=(f"🧪 *drydock self-distill — hard-overfit reproduction test DONE*\n\n"
     f"Task: distribution-search (overfit to loss→0, r=32) on matched HF-Q5 base, real TUI ×{total}\n"
     f"Runs: {detail}\n\n{verdict}\n\n"
     f"Loop relaunched: {'yes ✓' if loop_up else 'NOT yet (watchdog will within 15m)'}\n"
     f"Results: {out}")
url=f"https://api.telegram.org/bot{token}/sendMessage"
data=urllib.parse.urlencode({"chat_id":chat,"text":msg,"parse_mode":"Markdown"}).encode()
try:
    r=urllib.request.urlopen(url,data,timeout=20); print("TG_SENT", json.load(r).get("ok"))
except Exception as e:
    print("TG_FAILED", e)
PY
