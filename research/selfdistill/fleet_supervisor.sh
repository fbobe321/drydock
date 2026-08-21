#!/usr/bin/env bash
# fleet_supervisor.sh — keep the 3-box fleet from ever going idle.
#
# Run by cron every ~10 min (+ @reboot). flock → single instance. Model:
#   FOREGROUND EXPERIMENT active (compound_measure / ml_suite_sweep / probe_*) ?
#     → it owns the boxes; supervisor only reaps stale zombies and steps aside.
#   ELSE (baseline collection mode):
#     → ensure the cluster workers (20a,20b→.20, 21a→.21) are ALIVE (relaunch in
#       tmux on the current DD_VER), and NEVER let the queue drain (auto-enqueue
#       the next backlog batch). Reap zombies either way.
# Kill switch: touch cluster/STOP_FLEET to disable all supervisor action.
set -u
SD=/data3/tbench_local/frontier/selfdistill
CL="$SD/cluster"; source "$CL/lib.sh" 2>/dev/null
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
LOG="$SD/fleet_supervisor.log"
DD_VER="${DD_VER:-3.1.15}"
ZOMBIE_HRS="${ZOMBIE_HRS:-4}"          # a legit ratchet task is bounded by rounds×budget (~2h); >4h = stuck
BATCH="${BATCH:-16}"                    # backlog jobs to seed when the queue drains
say(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

# ── single instance ────────────────────────────────────────────────────────
exec 8>"$SD/.fleet_supervisor.lock"
flock -n 8 || { echo "[$(date '+%F %T')] another supervisor holds the lock — exit" >> "$LOG"; exit 0; }
[ -f "$CL/STOP_FLEET" ] && { say "STOP_FLEET set → supervisor idle"; exit 0; }

# ── zombie reaper (safe: only containers older than the max legit task time) ──
reap_zombies(){
  local c created age now; now=$(date +%s)
  for c in $(docker ps --filter 'name=ddt_' --format '{{.Names}}' 2>/dev/null); do
    created=$(docker inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null)
    [ -z "$created" ] && continue
    age=$(( now - $(date -d "$created" +%s 2>/dev/null || echo "$now") ))
    if [ "$age" -gt $(( ZOMBIE_HRS*3600 )) ]; then
      docker rm -f "$c" >/dev/null 2>&1 && say "REAPED zombie container $c (age $((age/3600))h)"
    fi
  done
}

# ── is a foreground experiment running? (it owns the fleet) ──────────────────
experiment_active(){
  # moe_train_eval.sh STOPS the .20 workers and its server to free the 48GB card for
  # QLoRA training — so it MUST be listed here, else this supervisor would relaunch
  # those workers mid-training and contend for the GPU it just freed.
  # moe_orchestrate.sh is included too: it DRAINS the .20 lanes on purpose (waiting for
  # in-flight jobs to end, with stopped workers deliberately left down) before handing
  # off to the trainer. If the supervisor kept relaunching those workers they'd claim
  # fresh jobs and the drain would never complete.
  pgrep -f 'compound_measure.sh|ml_suite_sweep.sh|probe_mr.sh|probe_band.sh|moe_train_eval.sh|moe_orchestrate.sh' >/dev/null 2>&1
}

# ── unified task pool: one namespace symlinking EVERY suite (+ generated) ─────
# Turns the finite tbench-2 pool into a large, growable one. Collection uses
# DDT_TASKS=$POOL, so adding a suite or a generated task = a new symlink here.
POOL=/data3/tbench_local/tasks/pool
POOL_SOURCES=(
  /data3/tbench_local/tasks/terminal-bench-2
  /data3/tbench_local/tasks/ml-suite
  /data3/tbench_local/feal_variants/tasks
  /data3/tbench_local/tasks/generated
)
rebuild_pool(){
  mkdir -p "$POOL" /data3/tbench_local/tasks/generated
  local src t
  for src in "${POOL_SOURCES[@]}"; do
    [ -d "$src" ] || continue
    for t in "$src"/*/; do
      [ -d "$t" ] || continue
      local name; name=$(basename "$t")
      [ -e "$POOL/$name" ] || ln -s "$t" "$POOL/$name" 2>/dev/null
    done
  done
}

# ── worker keepalive: relaunch a dead/missing worker in tmux (on the POOL) ────
ensure_worker(){   # ensure_worker <id> <server>
  local id="$1" server="$2"
  # LIVENESS = the tmux SESSION, not pgrep. A leftover `tmux new-session -d ... bash
  # cluster/worker.sh <id> ...` launcher keeps "worker.sh <id>" in its OWN cmdline
  # after the session dies, so `pgrep -f` returns a FALSE POSITIVE and the supervisor
  # silently never relaunches — lane 20a sat dead ~10h on 2026-08-21 this way, with
  # the box at 0% util. pgrep cannot distinguish launcher from worker (both cmdlines
  # contain the same string), so the session is the only reliable signal.
  if tmux has-session -t "wrk_$id" 2>/dev/null; then
    pgrep -f "bash cluster/worker.sh $id " >/dev/null 2>&1 && return 0
    say "worker $id: tmux session exists but no worker process — recycling"
    tmux kill-session -t "wrk_$id" 2>/dev/null
  fi
  # DO NOT try to "reap stale launcher PIDs" here. A `tmux new-session -d` process can
  # BE THE TMUX SERVER hosting every session — killing it takes down all workers AND
  # their ddt driver sessions mid-job. That exact mistake killed 3 lanes and orphaned
  # 2 in-flight jobs on 2026-08-21. Session-scoped operations only (kill-session is
  # targeted and safe); never kill a PID matched by cmdline here.
  # CRITICAL: close the supervisor's flock FD (8) in the spawned worker — else the
  # long-lived worker inherits it and holds the lock forever, locking the supervisor
  # out of every future tick (keepalive/refill silently dead). `exec 8>&-` drops it.
  # The trailing `8>&-` closes FD 8 for the TMUX PROCESS ITSELF, not just the inner
  # shell. When no server is running, `tmux new-session` BECOMES the server — which
  # then inherits the supervisor's flock FD and holds the lock forever, locking every
  # future tick out (keepalive/refill/auto-park all silently dead). The inner
  # `exec 8>&-` runs too late to prevent that: it only fixes the worker shell.
  # Cost when this bit on 2026-08-21: 4.5h of supervisor outage, campaign frozen.
  tmux new-session -d -s "wrk_$id" "exec 8>&-; cd $SD && DDT_TASKS=$POOL bash cluster/worker.sh $id $server $DD_VER" 8>&-
  say "RELAUNCHED worker $id → $server (dd_ver=$DD_VER, pool) in tmux wrk_$id"
}

# ── experiment queue: launch the next queued experiment when the fleet is free ─
# experiment_queue.tsv lines: "pending<TAB><launcher command>". We pop the first
# pending, launch it fire-and-forget (it becomes the foreground experiment that
# defers collection next tick), and mark the line launched. Consume-once.
EXPQ="$SD/experiment_queue.tsv"
launch_next_experiment(){
  [ -f "$EXPQ" ] || return 1
  local line cmd
  line=$(grep -nE '^pending\b' "$EXPQ" | head -1)
  [ -z "$line" ] && return 1
  local ln="${line%%:*}"; cmd=$(sed -n "${ln}p" "$EXPQ" | cut -f2-)
  [ -z "$cmd" ] && return 1
  sed -i "${ln}s/^pending/launched/" "$EXPQ"
  ( cd "$SD" && nohup bash -c "$cmd" >> "$SD/heredity/experiment_runs.log" 2>&1 & )
  say "LAUNCHED queued experiment: $cmd"
  return 0
}

# ── never-drain: seed the next backlog batch when the queue is empty ─────────
pending_count(){ awk -F'\t' '$6=="pending"' "$CL/queue.tsv" 2>/dev/null | wc -l; }
_attempted(){ grep -qE "^$1," "$SD/ratchet/ratchet_results.csv" "$SD/heredity/ml_suite_results.csv" 2>/dev/null; }
_solved_anywhere(){ [ -e "$SD/ratchet/$1_solved_traj.json" ]; }
# only skip a task that is CURRENTLY pending/running — NOT one with an old done/
# failed row, else previously-attempted-but-unsolved tasks would never be re-tried
# (which is the entire point of the escalation campaign).
_queued(){ awk -F'\t' -v t="$1" '$3==t && ($6=="pending"||$6=="running"){f=1} END{exit f?0:1}' "$CL/queue.tsv" 2>/dev/null; }
seed_fresh(){   # enqueue up to BATCH never-attempted, unsolved, unqueued pool tasks
  local seeded=0 name t
  for t in "$POOL"/*/; do
    [ -d "$t" ] || continue; name=$(basename "$t")
    _solved_anywhere "$name" && continue
    _attempted "$name" && continue
    _queued "$name" && continue
    q_enqueue collect "$name" 6 900 >/dev/null 2>&1 && seeded=$((seeded+1))
    [ "$seeded" -ge "$BATCH" ] && break
  done
  echo "$seeded"
}
# ── THE CAMPAIGN: solve terminal-bench-2 completely, escalating effort per task ─
# Each unsolved task climbs an effort ladder every full cycle it survives:
#   tier0 ratchet×8 → tier1 ratchet×12 → tier2 eratchet×12 → tier3+ eratchet×16.
# So a task the plain ratchet can't crack eventually gets the evolutionary ratchet
# at max rounds, repeatedly — "might take a long time" but never gives up.
TBENCH=/data3/tbench_local/tasks/terminal-bench-2
EFFORT="$SD/heredity/tbench_effort.tsv"
STUCK_ALERTED="$SD/heredity/stuck_alerted.txt"   # comms: tasks we've phoned home about
# FLATLINE PARK: tasks that completed an eratchet run still at best=0 (0 partial,
# 1 niche) have NO gradient for evolution to climb — every round restarts from a
# 0-progress state. Parking them stops tier-3 eratchet cycles (~3-4h each) being
# burned where they structurally can't crack, freeing the lanes for gradient-
# bearing near-misses (path-tracing 4/5, video-processing 3/5, ...). Reversible:
# a parked task re-enters the campaign the moment the base improves — clear this
# file on a self-distill promotion (a stronger base may finally show partial
# credit). Manual un-park: `> heredity/parked_flatlines.txt`.
PARKED="$SD/heredity/parked_flatlines.txt"; touch "$PARKED"
EVOLVE_CSV="$SD/ratchet/ratchet_evolve_results.csv"   # rows: task,solved,best,total
_parked(){ grep -qxF "$1" "$PARKED" 2>/dev/null; }
# flatlined = the task HAS a completed eratchet row and its LATEST one is
# solved=0 AND best=0. `f` guards the not-found case: an uninitialized awk var
# equals 0, so without it a task absent from the CSV would falsely read flatline.
_eratchet_flatlined(){ awk -F, -v t="$1" '$1==t{s=$2;b=$3;f=1} END{exit (f&&s==0&&b==0)?0:1}' "$EVOLVE_CSV" 2>/dev/null; }
tb_solved(){ [ -e "$SD/ratchet/$1_solved_traj.json" ] || [ -e "$SD/ratchet/$1_evolved_traj.json" ]; }
get_tier(){ awk -F'\t' -v t="$1" '$1==t{v=$2} END{print v+0}' "$EFFORT" 2>/dev/null; }
set_tier(){ { grep -vP "^$1\t" "$EFFORT" 2>/dev/null; printf '%s\t%s\n' "$1" "$2"; } > "$EFFORT.tmp" && mv "$EFFORT.tmp" "$EFFORT"; }
tier_params(){ case "$1" in 0) echo "collect 8 1200";; 1) echo "escalate 12 1500";; 2) echo "eratchet 12 1500";; *) echo "eratchet 16 1800";; esac; }
seed_tbench_escalation(){
  touch "$EFFORT"; local seeded=0 d name tier type rounds budget
  for d in "$TBENCH"/*/; do
    [ -d "$d" ] || continue; name=$(basename "$d")
    tb_solved "$name" && continue           # already solved → out of the campaign
    _queued "$name" && continue             # already in the queue this cycle
    _parked "$name" && continue             # flatlined under eratchet → parked until base improves
    tier=$(get_tier "$name")
    # AUTO-PARK: once a task reaches max effort (tier 3 = eratchet) and its latest
    # eratchet run flatlined (solved=0, best=0 → no partial gradient), stop feeding
    # it eratchet cycles — record it in the park and skip. Evidence-based, so it
    # only fires after the task has actually had its shot at the top tier.
    if [ "$tier" -ge 3 ] && _eratchet_flatlined "$name"; then
      grep -qxF "$name" "$PARKED" 2>/dev/null || { echo "$name" >> "$PARKED"; say "PARKED flatline $name (eratchet best=0 — no gradient; excluded until base improves)"; }
      continue
    fi
    # COMMS dogfood: a task at MAX effort (tier 3 = eratchet×16) has resisted the
    # whole ratchet→eratchet ladder → phone the operator home via drydock.comms,
    # once per task (the attention layer floors agent.stuck/APPROVAL_REQUIRED to a
    # message, so it actually reaches them).
    if [ "$tier" -ge 3 ] && ! grep -qxF "$name" "$STUCK_ALERTED" 2>/dev/null; then
      bash "$SD/comms_alert.sh" "$name" \
        "$name resisted ratchet + eratchet across every effort tier and is now at max rounds. Intervene (env change / skip) or let it keep grinding?" >/dev/null 2>&1
      echo "$name" >> "$STUCK_ALERTED"
      say "COMMS: agent.stuck sent for $name (hit max effort tier)"
    fi
    read -r type rounds budget <<<"$(tier_params "$tier")"
    if q_enqueue "$type" "$name" "$rounds" "$budget" >/dev/null 2>&1; then
      seeded=$((seeded+1)); set_tier "$name" $(( tier<3 ? tier+1 : 3 ))   # escalate for next cycle
    fi
    [ "$seeded" -ge 100 ] && break
  done
  echo "$seeded"
}
refill_backlog(){
  # PRIORITY: finish terminal-bench-2 before any other benchmark.
  local seeded; seeded=$(seed_tbench_escalation)
  if [ "${seeded:-0}" -gt 0 ]; then say "CAMPAIGN: seeded $seeded tbench-2 jobs (escalating ratchet→eratchet)"; return; fi
  # 0 seeded — but distinguish "all solved" from "residue is all PARKED flatlines".
  # If any parked task is still unsolved, tbench-2 is NOT complete: hold (don't
  # advance to the next benchmark) and wait for a base improvement to un-park them.
  local held=0 n
  while IFS= read -r n; do [ -n "$n" ] && ! tb_solved "$n" && held=$((held+1)); done < "$PARKED"
  if [ "$held" -gt 0 ]; then
    say "HOLD: 0 seeded, but $held parked flatline(s) still unsolved — tbench-2 NOT complete; retry on base improvement"
    return
  fi
  # tbench-2 has no unsolved tasks left → benchmark COMPLETE. Advance to the next.
  say "🎉 terminal-bench-2 COMPLETE — advancing to the next benchmark (unified pool)"
  rebuild_pool; seeded=$(seed_fresh)
  if [ "${seeded:-0}" -gt 0 ]; then say "seeded $seeded next-benchmark tasks"; return; fi
  if bash "$SD/gen_pool.sh" "$BATCH" >> "$LOG" 2>&1; then rebuild_pool; seeded=$(seed_fresh); [ "${seeded:-0}" -gt 0 ] && { say "generated + seeded $seeded"; return; }; fi
  bash "$CL/enqueue.sh" escalation >/dev/null 2>&1 && say "re-seeded escalation (all pools exhausted)"
}

# ── main ─────────────────────────────────────────────────────────────────────
reap_zombies
if experiment_active; then
  say "foreground experiment active → collection deferred (reaped zombies only)"
  exit 0
fi
# fleet is FREE — first drain the experiment queue (a launched experiment becomes
# the foreground work and defers collection on the next tick).
if launch_next_experiment; then
  exit 0
fi
# no queued experiment → baseline collection mode
ensure_worker 20a http://192.168.50.20:8000/v1
ensure_worker 20b http://192.168.50.20:8000/v1
ensure_worker 21a http://192.168.50.21:8000/v1
p=$(pending_count)
if [ "${p:-0}" -eq 0 ]; then
  say "queue drained (pending=0) → refilling backlog"
  refill_backlog
fi
say "tick ok (mode=collection, pending=$(pending_count))"
