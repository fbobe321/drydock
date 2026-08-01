#!/usr/bin/env bash
# selfdistill_watchdog.sh — keep the loop alive across crashes/reboots. Cron: every 15min
# + @reboot. Race-free single-instance guarantee comes from selfdistill_launch.sh's
# `flock -n`; this watchdog just detects a dead loop and relaunches it.
set -u
SD=/data3/tbench_local/frontier/selfdistill
LOG="$SD/watchdog.log"
STAMP(){ date '+%F %T'; }
say(){ echo "[$(STAMP)] $*" >> "$LOG"; }

# Is a loop holding the lock? If flock -n succeeds in a subshell, the lock is FREE => dead.
if flock -n "$SD/loop.lock" true 2>/dev/null; then
  # loop is DOWN. But do NOT relaunch into an out-of-loop training run (a validation or
  # manual retrain that stopped the server to use the GPU) — the loop would restart the
  # server and contend for VRAM -> OOM. Defer until that training finishes.
  if pgrep -f 'compass train' >/dev/null 2>&1 || pgrep -f 'sd_train_oneshot' >/dev/null 2>&1; then
    say "loop down but an out-of-loop training run is active — deferring relaunch"
    exit 0
  fi
  say "loop DOWN — relaunching"
  setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 &
  disown
  say "relaunch issued (pid $!)"
else
  : # lock held => loop alive => nothing to do
fi
