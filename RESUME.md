# Drydock v3 — Session Resume / Handoff

The work runs on the workstation; you steer it remotely via Claude Code's
Remote Control.

- **Resume the actual conversation (full context):** on the workstation,
  `cd /data3/drydock-v3 && claude --resume "drydock"` (or `claude --continue`).
  History lives in `~/.claude/projects/…` and comes back intact.
- **Control it from a phone/laptop:** enable once via `claude` → `/config` →
  "Enable Remote Control for all sessions" → true; then a resumed session shows
  up at claude.ai/code (or the Claude app → Code). Execution stays on this box;
  the remote device is just the UI. Needs a claude.ai subscription + `/login`
  (API keys don't work); the box must stay on with the `claude` process running.

- **Keep it alive untended — run it in `tmux`** so closing the terminal or an
  SSH drop doesn't kill the `claude` process:
  ```bash
  tmux new -s dd                                   # start a named session
  cd /data3/drydock-v3 && claude --resume "drydock"   # remote control auto-on if enabled
  # detach (leave it running):  Ctrl-b then d
  # later, reattach from an SSH login:  tmux attach -t dd
  ```
  Detaching keeps `claude` running, so the phone/web link stays live. Only an
  actual reboot or `tmux kill-session` stops it — and even then your code is
  committed/pushed and the conversation is resumable, so nothing is lost.

> **Nothing here is lost by stopping.** Code = committed + pushed to the private
> repo. Conversation = saved under `~/.claude/projects/…`, replayed by
> `claude --resume`. Only the live remote-control *link* needs the process up;
> restart `claude --resume` to get a fresh one.

This file is the **fallback primer** for starting a brand-new session without
that history — paste it as the first message.

---

## What this is

Drydock v3 is a **clean-room, Apache-2.0, provider-agnostic terminal coding
agent** for local LLMs. It is NOT a fork — it replaces the v2 line (a
`mistral-vibe` fork whose inherited phone-home code got the PyPI account
quarantined). The whole point of v3 is clean IP provenance owned end to end.

- **Repo:** `https://github.com/fbobe321/drydock-v3` (PRIVATE), branch `master`.
- **Checkout:** `/data3/drydock-v3` on the workstation.
- **Primary model (UPDATED 2026-06-22):** **Gemma-4-31B dense** (QAT
  `Q4_K_XL`, NOT the 26B-A4B MoE) served by llama.cpp at
  `http://localhost:8000/v1` (model name `gemma4`). Swapped because the
  26B-A4B's ~4B active params caused fatal agentic loops (180× identical
  pytest). The dense 31B is loop-free; ~3–4× slower (15 tok/s tensor-split
  vs 64) but it FINISHES. Vision via matching `mmproj-gemma4-31b-F16.gguf`.

---
## ⭐ 2026-06-26 — LATEST STATE (READ THIS FIRST)

**Direction locked by the operator (2026-06-26):**
- **v3 (`/data3/drydock-v3`) is THE codebase.** The old `/data3/drydock`
  mistral-vibe fork is DEAD and RADIOACTIVE — do **NOT** read it, run it, or
  copy/port anything out of it. It is the inherited lineage/phone-home code that
  got the PyPI account banned. v3 stays **from-scratch / clean-room**. Missing
  features get **built fresh in v3**, never ported.
- **Improve drydock by USING its real TUI, hands-on (tmux).** `-p`,
  programmatic, pexpect, and stress harnesses do **NOT** count (rules #1 & #4
  below). Operator, verbatim: *"it has to go through the TUI. That is the whole
  point."*

**tbench was STOPPED (2026-06-26) — and why it matters:** the entire tbench
setup drove drydock via `drydock -p '<task>'` (one-shot). That exercises the
model + `agent.py` core but **bypasses the TUI entirely** — so the scores never
reflected the product the operator actually uses. Accordingly:
- Both harbor runs killed (`drydock_PASS3_v3.0.45_mult6_3box` + a
  `RERUN_FAILS` run). Relaunch crons PAUSED:
  `/data3/drydock/.pause_harbor_watchdog`, `.pause_tbench_chain`,
  `.pause_tbench_watchdog`.
- ~58 GB docker reclaimed (unused images + build cache); the `gemma4` server
  container/image preserved. Job result dirs (~1.3 GB) left at
  `/data3/tbench_local/jobs`.
- Last `-p` numbers (informational only, NOT a TUI measurement): pass@1 = 21/89
  (23.6%); the stopped pass@3 was ~25/89 (28.1%, partial).

**🎯 PRIMARY GOAL — TUI-driven testing (open task):** build a way to run tbench
TASKS through v3's **real TUI** (not `-p`): set up the task environment, launch
v3's TUI in tmux, feed the task prompt, let drydock work through the genuine TUI
code path, then run the task's existing verifier — verify by watching
`capture-pane`. Honor "no custom eval harness": reuse the real task defs +
verifiers and drive the genuine TUI; do not build a judge/batch-runner pipeline.

**Open task — build `/compact` FRESH in v3:** `drydock/cli.py` advertises
`/compact` in `/help` (line ~158) but `handle_command` never implements it.
Wire it to the existing `compaction.py` (`maybe_compact`/`emergency_compact`) +
the TUI, and verify hands-on in tmux. Pure from-scratch v3 work.

**⚠️ Heads-up (do NOT act on without operator OK):** the dead fork still has an
`auto_release` cron that could republish the banned-lineage package to PyPI.
Worth confirming it's disarmed — but check only the cron/flag, never the old code.

**Mistake not to repeat:** on 2026-06-25, before this decision, two fixes (a
read-timeout fix + a thinking-visibility feature) were committed into the OLD
fork `/data3/drydock` — the wrong tree. They are NOT in v3 and must NOT be
ported as code. v3 already shipped its own bash/read-timeout handling (v3.0.45,
`df51a06`). If those behaviors are wanted in v3's TUI, build them fresh.

---

## ⭐ 2026-06-24 OVERNIGHT — (history; superseded by the 2026-06-26 section above)

**Repo HEAD:** `93fbb46` (v3.0.44 + PRD update), CI **green**, all pushed. Repo PRIVATE.

**Fleet (all 4 boxes on dense 31B + vision, 64K ctx, persistent):**
- remus/Dell `.22:8000` (docker `llamacpp-gemma4-31b`, this box), romulus
  `.21:8000` (systemd), 3090 `.129:8000` (Windows `fbobe@`, watchdog
  scheduled-task **every 24h**), Jetson `.19:8080` (systemd, `/opt/models` NVMe).
- **Jetson caution:** under the 31B it *thrashes* (5 tok/s) until SSH +
  llama-server both time out, though the host stays up (17d uptime). It
  recovers when idle. Excluded from scoring runs (it converts passable tasks
  to false-timeouts). The harbor adapter now health-probes + skips dead boxes.
- Rollback to 26B-A4B: `/data3/Models/gemma4_restore_config.txt`.

**Shipped 2026-06-24 (v3.0.39→44, all CI-green + pushed):**
- v3.0.39 concrete default `base_url`; v3.0.40 Python floor 3.12→3.11
  (v2-upgrade compat); **v3.0.41 active v2→v3 config migration** (back up
  legacy/foreign config.toml, write fresh editable one — verified on the real
  v2 config on the Windows 3090).
- **v3.0.42** `-p`/one-shot surfaces unreachable LLM as the actionable message
  + exit 2 (was a raw traceback); purged `qwen` from test fixtures.
- **v3.0.43** plan panel cleared at each user-turn start (stale-plan bug, found
  by hands-on TUI use, verified live).
- **v3.0.44** `-p` trace logs tool inputs + outcomes (timeout diagnosability).
- harbor_fork adapter: **skips unreachable backends** in the rr pool; wheel
  launch helper `/data3/tbench_local/tbench_launch_lib.sh` (never-404).
- Fixed the operator's live `~/.drydock/config.toml` (qwen→gemma4 + base_url);
  de-flaked a CI-flaky TUI test; refreshed editable install → `--version` 3.0.44.

**tbench harness** (`/data3/harbor_fork`, adapter `.../installed/drydock_agent.py`).
Container networking was fixed earlier (ufw `DEFAULT_FORWARD_POLICY=ACCEPT` +
docker restart + iptables `INPUT -s 172.16.0.0/12 --dport 8899 ACCEPT`). Wheel
served by host `python -m http.server 8899` over `/data3/drydock-v3/dist/`.
- **Use the launch helper:** `source /data3/tbench_local/tbench_launch_lib.sh;
  tb_prepare_wheel 3.0.44` (builds-if-missing, verifies HTTP 200, sets
  `DRYDOCK_INSTALL_SPEC`). Pool env `DRYDOCK_BACKEND_POOL=<comma list>`; rr
  counter `/tmp/dd_backend_rr.ctr`. Eval: `result.json` →
  `stats.evals[ev].reward_stats.reward` (1.0=pass); `is_resolved` is unreliable.

**Results (dense 31B, full 89-task terminal-bench-2 corpus):**
- **pass@1 = 21/89 ≈ 23.6%** (clean 3-box, Jetson excluded). V2PASSED10 = 8/10.
- **pass@3 IN PROGRESS** (extended `--agent-timeout-multiplier 6`): job
  `drydock_PASS3_v3.0.44_mult6_3box`, 68 not-yet-passing tasks ×3 (21 pass@1
  winners carried forward in `/tmp/pass3_winners.json`), 3-box pool. Monitor
  `/tmp/pass3_progress.log`. Was ~29% climbing when the operator went to bed.
  Merge = winners ∪ retry-rescued. Self-monitoring; report the headline + which
  tasks the extended timeout rescued when it finishes (~20-40h run).

**Doom-loop verdict vs 26B-A4B (data, 219-239 trials + hands-on TUI):** the
dense 31B does NOT reproduce the 26B's hard tool-loops. Byte-identical guard
(`agent.py IDENTICAL_REPEAT_CAP=8`) fired 36/239 trials, caps at 8; ~85% never
trip it; 0 loops in ~8 hands-on TUI tasks. **One rare different failure:**
text-repetition collapse (make-mips-interpreter emitted `295:` ×1365), 1 task /
219, not seen by the tool guard. **Task #41 (queued, deferred until fleet is
free):** server-side `--repeat-penalty 1.1` (+ optional `--dry-multiplier 0.8`)
in `/data3/Models/start_gemma4_31b_llamacpp.sh`, and/or a drydock-side text-loop
guard. Server currently runs with NO repetition control.

**Open caveat:** 31B is slow on hard frontier tasks (compiler builds, ray
tracers, ARC-AGI) → genuine timeouts even at mult 6 (NOT loops — diagnosed).
Lever = lower server `--reasoning-budget` (currently 20000), operator's call.
---

## Current state (as of HEAD 956e017)

- **177 tests pass, ruff + pyright clean, release wheel builds 0 HIGH findings.**
- Local == `origin/master`, nothing uncommitted/unpushed.
- Feature-complete vs the original task list + the major v2 reliability
  hardening is ported. Backlog #1–#27 all done.
- 13 shakedowns passed (6 medium + 5 hard + 2 failure-recovery), zero
  text-form tool-call leaks.

### What's implemented
- TUI (Textual): transcript, streamed text, collapsible tool cards, multi-line
  prompt (`Ctrl+J` newline, `Enter` submits), `↑/↓` history (persists to
  `~/.drydock/history`), `Ctrl+O` expand tools.
- Slash commands: `/model /cwd /undo /back /status /clear /help /quit`.
- Config: `~/.drydock/config.toml` (defaults < file < CLI flags; NEVER modifies
  an existing file — only creates on first run). First-launch local-LLM
  autodetect (probes :8000/:11434/:1234).
- Safety: catastrophic-command denylist (`rm -rf /`, `mkfs`, fork bombs — hard
  refused) + approval modal for sensitive commands (`sudo`, installs, network,
  `git push` → Allow/Always/Deny).
- Guards (advisory, never block): syntax, main-entry, stub-only, missing
  sibling imports, bare-raise; conflict-marker Write/Edit refusal.
- Gemma reliability: non-streaming on tool turns, thinking/special-token
  stripping, hallucinated-tool redirect, text-form tool-call recovery + retry,
  adaptive `reasoning_effort` (high-to-plan / low-to-continue), loop nudges +
  same-path write-thrash advisory, blank/dir-path guard, two-tier compaction.

### Module map (`drydock/`)
`agent.py` (loop, adaptive reasoning, hallucinated-tool redirect, drop_last_turn) ·
`providers.py` (OpenAI-compatible, non-streaming for Gemma tools, unreachable
error) · `bash_safety.py` (denylist + approval tier) · `guards.py` (advisory
write guards + conflict markers) · `loop_detect.py` · `tuning.py` (Gemma tuning) ·
`compaction.py` · `config.py` · `detect.py` · `tools/__init__.py` (Read/Write/
Edit/Bash/Glob/Grep + undo journal) · `tui/{app,widgets,approval,messages}.py`.

## How to run / test / verify

On the **workstation** (this is where it runs — Python at
`/home/bobef/miniconda3/bin/python3`, has textual/openai):

```bash
cd /data3/drydock-v3
# Run the TUI (in a project dir; model on local :8000)
PYTHONPATH=/data3/drydock-v3 /home/bobef/miniconda3/bin/python3 -m drydock --model gemma4 --provider vllm
# Tests — the local pytest_cov plugin is broken, so disable it:
/home/bobef/miniconda3/bin/python3 -m pytest tests/ -q -o addopts= -p no:cov -p no:cacheprovider   # 177 pass
/home/bobef/miniconda3/bin/python3 -m ruff check drydock/ tests/
/home/bobef/miniconda3/bin/python3 -m pyright --pythonpath /home/bobef/miniconda3/bin/python3 drydock/
DRYDOCK_PY=/home/bobef/miniconda3/bin/python3 ./scripts/release.sh   # build + security scan
```

(Fresh clone elsewhere instead: `python3 -m venv .venv && . .venv/bin/activate
&& pip install -e ".[dev]"`, then `drydock` / `pytest tests/ -q` / `ruff` /
`pyright` are on PATH and need none of the workstation flags.)

## NON-NEGOTIABLE working rules (learned the hard way)

1. **Verify harness-side fixes in a REAL tmux TUI session** — not just pytest.
   `tmux new-session -d -s v3 -x 200 -y 50 'cd <dir> && PYTHONPATH=/data3/drydock-v3 /home/bobef/miniconda3/bin/python3 -m drydock --model gemma4 --provider vllm'`,
   then `tmux send-keys` a real prompt, `tmux capture-pane -p` to read it, and
   run the built code functionally. `--help` is NOT a test.
2. **Isolate `HOME` for TUI tests:** prepend `HOME=/tmp/v3_home` (mkdir it).
   This box also runs the operator's v2 install, and both share
   `~/.drydock/{config.toml,history}` — a dead-port test once leaked `base_url`
   into the operator's real config. Always sandbox HOME for test runs.
3. **Never let ruff/lint autofix delete side-effect imports.** `ruff --fix` once
   removed `import drydock.tools` from agent.py (it registers the tools), leaving
   an empty registry → the model emitted tool calls as TEXT and nothing ran.
   agent.py now calls `register_all()` explicitly with a regression test.
4. **NO custom eval harnesses.** Drive the TUI by hand, one prompt at a time.
5. **Advisory, never blocking** — loop/guard mechanisms inject context; only
   hard stops are the catastrophic denylist and max-tool-turns.
6. **Loop-breakers return a result string, never raise** (raising spawns its own
   loop on long tasks).
7. `scripts/security_scan.py` gates every release (exit 2 = HIGH = block).

## Credentials / env (workstation)
- GitHub: token at `~/.config/drydock/github_token` (user `fbobe321`); `gh` at
  `/home/bobef/miniconda3/bin/gh`. To push, set `GH_TOKEN` and use a one-shot
  credential helper (the repo has no stored creds).
- Dev/test Python: `/home/bobef/miniconda3/bin/python3` (3.12, has textual/openai).
- tbench notifications paused via `/data3/drydock/.pause_tbench_*` flags.
- PyPI reinstatement appeal draft: `/data3/drydock/docs/pypi_reinstatement_appeal.md`.

## Suggested next steps (pick up here — 2026-06-26)
1. **Hands-on v3 TUI shakedown** — drive v3's real TUI in tmux (per rule #1),
   establish a clean baseline of what works / what's missing in the from-scratch
   build, with `capture-pane` evidence. This is how we find what to build next.
2. **Build `/compact` fresh in v3** (advertised in `/help`, not implemented —
   wire to `compaction.py`). Verify in tmux.
3. **Build the TUI-driven test path** (PRIMARY GOAL above): run tbench tasks
   through v3's real TUI instead of `-p`, then use it to find + fix real bugs.
4. Do NOT touch / read / port from the old `/data3/drydock` fork.
5. (Operator action, unchanged) PyPI/Docker republish per the reinstatement
   status; confirm the dead fork's `auto_release` cron can't republish it.

Earlier backlog (v3 hardening port, retrieval, etc.) is essentially done — see
the "What's implemented" + history sections above.
