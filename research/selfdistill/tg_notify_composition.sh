#!/usr/bin/env bash
# tg_notify_composition.sh — wait for the composition diagnostic, Telegram the verdict (PLAIN TEXT).
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/composition_results.csv"; LOG="$SD/composition.log"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"
for i in $(seq 1 720); do pgrep -f 'sd_train_oneshot_composition' >/dev/null 2>&1 || break; sleep 30; done
/home/bobef/miniconda3/bin/python3 - "$OUT" "$LOG" "$TOKEN" "$CHAT" <<'PY'
import sys, urllib.request, urllib.parse, json, subprocess, re
out, log, token, chat = sys.argv[1:5]
rows=[]
try:
    for l in open(out):
        p=l.strip().split(",")
        if len(p)<3 or p[0]=="config": continue
        rows.append((p[1],p[2]))
except Exception: pass
s=sum(1 for _,r in rows if r=="1"); n=len(rows)
mll=""
try:
    for line in open(log):
        m=re.search(r"mean_last_10': ([0-9.eE+-]+)", line)
        if m: mll=m.group(1)
except Exception: pass
loop_up = subprocess.run("pgrep -f 'flock.*loop.lock'", shell=True, capture_output=True).returncode==0
# file-write vs run-based split for interpretation
FW={"break-filter-js-from-html","custom-memory-heap-crash","large-scale-text-editing","constraints-scheduling"}
fw_s=sum(1 for t,r in rows if t in FW and r=="1"); fw_n=sum(1 for t,_ in rows if t in FW)
if not rows: verdict="no rows — check composition.log"
elif s>=5: verdict=(f"COMPOSITION FIXED by rank — {s}/{n} trained tasks reproduce at r=128 (was 0/7 at r=32). "
                    f"Rank is the lever: scale rank with corpus size. P2 volume plan is viable.")
elif s>0: verdict=(f"PARTIAL — {s}/{n} reproduce at r=128 (file-write {fw_s}/{fw_n}). Rank helps but doesn't "
                   f"fully fix multi-task composition; needs more (routing / per-domain adapters / higher rank).")
else: verdict=(f"STILL 0/{n} at r=128 (loss {mll}). Multi-task composition is a DEEP problem — 4x rank + "
               f"moderate overfit did not restore retrieval. Rank is not the (only) lever; rethink approach "
               f"(per-task routing, generalization-via-volume instead of memorization, or curriculum).")
detail=" ".join(f"{t}={r}" for t,r in rows)
msg=(f"drydock self-distill — COMPOSITION diagnostic DONE (7 P1-condensed traces, r=128, loss~{mll})\n\n"
     f"Reproduction (trained tasks): {s}/{n}\n{detail}\n\n{verdict}\n\n"
     f"(Baseline was r=32 -> 0/7 trained despite loss 3e-5.) Loop relaunched: {'yes' if loop_up else 'NOT yet'}. Results: {out}")
data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()   # plain text
try: r=urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",data,timeout=20); print("TG_SENT", json.load(r).get("ok"))
except Exception as e: print("TG_FAILED", e)
PY
