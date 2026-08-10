# 3-box task-parallel cluster (work-queue scheduler)

The boxes (`.20` RTX 8000 48GB, `.21` vLLM, `.22` 2×4060Ti) are **WiFi-linked (~112 Mbps)**
with **separate local `/data3`**. That interconnect rules OUT tightly-coupled distributed
training (DDP/FSDP sync every step = GB of traffic) — measured, not assumed. So this is a
**task-parallel cluster**: one shared queue on `.22` (coordinator), workers drain it, and the only
cross-box traffic is tiny job/result rows (KB). Throughput scales ~linearly for independent work
(collection, per-box training experiments, evals). **Wiring to GbE is slated FUTURE WORK** — only
needed if we ever want multi-node training; irrelevant for task-parallel.

## Pieces
- `lib.sh` — shared state + atomic queue ops (`q_enqueue`/`q_claim`/`q_complete`), all under
  `flock` on `queue.lock` so concurrent workers never corrupt `queue.tsv`.
- `queue.tsv` — jobs: `jobid  type  task  rounds  budget  status  worker  claimed_ts  done_ts  result`
  (`type` = escalate|collect|train|eval; `status` = pending|running|done|failed).
- `results.tsv` — append-only completion log.
- `enqueue.sh` — `enqueue.sh job <type> <task> [rounds] [budget]` or `enqueue.sh escalation`
  (seeds all unsolved near-miss partials, closest-first, excluding solved-anywhere/queued).
- `worker.sh <id> <server_url> [dd_ver]` — claims → runs `ratchet_solve` locally driving the
  assigned server → writes result → repeats. Skips a task whose `ddt_<task>` container exists
  (cross-worker collision guard). Idle-polls when drained; stops on `touch cluster/STOP`.
- `status.sh` — queue counts, running jobs, worker heartbeats, server health.

## Run a worker (IMPORTANT: launch in tmux)
`ratchet_solve` drives the TUI via `tmux send-keys`, so a worker needs a real session. Launch in
a **detached tmux** session (bare `setsid`/`nohup` dies). Also: **never `pkill -f` a pattern that
appears in your own launch command** — pkill matches the launching shell's cmdline and kills it
(exit 144). Kill workers by tmux session or PID instead.

```bash
tmux new-session -d -s cl20a "bash /data3/tbench_local/frontier/selfdistill/cluster/worker.sh 20a http://192.168.50.20:8000/v1 3.0.138"
tmux new-session -d -s cl21a "bash .../cluster/worker.sh 21a http://192.168.50.21:8000/v1 3.1.8"   # vLLM needs 3.1.8
tmux new-session -d -s cl22a "bash .../cluster/worker.sh 22a http://localhost:8000/v1 3.0.138"      # only when .22 not training
bash cluster/status.sh          # watch
touch cluster/STOP              # graceful stop of all workers (rm to resume)
```

## Current state (2026-08-09)
- **`.20` LIVE on the scheduler** (worker `20a`, escalation queue seeded, 34 jobs). Replaced the
  manual `ratchet_20_sweep`.
- **`.21`** still on its manual `ratchet_21_sweep` (productive; migrate to a `21a` worker when it
  finishes its current pass).
- **`.22`** running the per-species heredity training; add a `22a` worker only when it's idle
  (training and a local-server worker contend for the same GPU).

## Phase 2 (future)
Deploy the harness (`tui_task_lib.sh` + tasks + a drydock wheel) to `.20`/`.21` so their workers
run ddt containers **locally** (each box uses its own CPUs), instead of all containers running on
`.22`'s 4-core host (the current concurrency cap). Then + GbE wiring, the fleet is a full cluster.
