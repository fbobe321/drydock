#!/usr/bin/env bash
# selfdistill_signal.sh — weeks-long grind monitor. Cron (every 6h). Telegrams the operator ONLY on
# MEANINGFUL events so a multi-week run doesn't spam: a HELD-OUT generalization hit, a PROMOTION, a
# corpus-size milestone, plus a once-daily still-alive heartbeat. Idempotent via .signal_state.
set -u
SD=/data3/tbench_local/frontier/selfdistill
EVALCSV="$SD/eval_results.csv"; CHAMP="$SD/champion.txt"; STATE="$SD/.signal_state"
CORPUS="$SD/corpus/raw"
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"   # scrubbed when vendored
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniconda3/bin"
tg(){ /home/bobef/miniconda3/bin/python3 - "$1" <<'PY'
import sys,urllib.request,urllib.parse,json
TOKEN="${TG_TOKEN:-}"; CHAT="${TG_CHAT:-}"
data=urllib.parse.urlencode({"chat_id":CHAT,"text":sys.argv[1]}).encode()
try: urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data,timeout=20)
except Exception as e: print("tg fail",e)
PY
}
# load reported state
GEN_REPORTED=0; PROMO_REPORTED=0; MILE_REPORTED=0; LAST_HB=0
[ -f "$STATE" ] && . "$STATE"

corpus=$(find "$CORPUS" -name '*.json' 2>/dev/null | wc -l)
gen=$(grep -oE 'gen=[0-9]+' "$SD/heartbeat.txt" 2>/dev/null | grep -oE '[0-9]+' | tail -1); gen=${gen:-?}
champ=$(cat "$CHAMP" 2>/dev/null || echo base)

# 1) GENERALIZATION: any eval row with heldout_solves>0 (col 3), gen (col1) newer than reported
best_ho=0; best_gen=0
while IFS=, read -r g adapter hs ht rest; do
  [ "$g" = gen ] && continue
  [ -z "${hs:-}" ] && continue
  if [ "$hs" -gt 0 ] 2>/dev/null && [ "$g" -gt "$GEN_REPORTED" ] 2>/dev/null; then
    tg "🎉 GENERALIZATION SIGNAL — gen $g held-out $hs/$ht (base failed these). $adapter. THE compounding bet is paying off. eval_results.csv"
    GEN_REPORTED=$g
  fi
  [ "${hs:-0}" -gt "$best_ho" ] 2>/dev/null && { best_ho=$hs; best_gen=$g; }
done < "$EVALCSV" 2>/dev/null

# 2) PROMOTION: champion left base
if [ "$champ" != base ] && [ "$PROMO_REPORTED" != 1 ]; then
  tg "⬆️ PROMOTION — champion is now $(basename "$champ") (a LoRA that transfers without breaking easy tasks). It now drives collection."
  PROMO_REPORTED=1
fi

# 3) corpus milestones
for m in 15 25 40 60; do
  if [ "$corpus" -ge "$m" ] && [ "$MILE_REPORTED" -lt "$m" ]; then
    tg "📈 corpus reached $corpus traces (milestone $m). Bigger corpus = better generalization odds. gen=$gen"
    MILE_REPORTED=$m
  fi
done

# 4) daily heartbeat (>=20h since last)
now=$(date +%s)
if [ $(( now - LAST_HB )) -ge 72000 ]; then
  loop=$(pgrep -f 'flock -n /data3/tbench_local/frontier/selfdistill/loop.lock' >/dev/null && echo up || echo DOWN)
  tg "🫀 self-distill grind alive: loop=$loop gen=$gen corpus=$corpus champion=$(basename "$champ") best-heldout=$best_ho/6. (Daily heartbeat; you'll get a ping the moment held-out flips or a promotion happens.)"
  LAST_HB=$now
fi

printf 'GEN_REPORTED=%s\nPROMO_REPORTED=%s\nMILE_REPORTED=%s\nLAST_HB=%s\n' \
  "$GEN_REPORTED" "$PROMO_REPORTED" "$MILE_REPORTED" "$LAST_HB" > "$STATE"
