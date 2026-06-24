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
## ⭐ 2026-06-23/24 OVERNIGHT — LATEST STATE (read this first on resume)

**Repo HEAD:** `5fe9b22` (v3.0.38), CI **green**, all pushed. Repo PRIVATE.

**Fleet (all 4 boxes on dense 31B + vision, 64K ctx, persistent):**
- remus/Dell `.22:8000` (docker `llamacpp-gemma4-31b`, this box), romulus
  `.21:8000` (systemd, tensor-split, gpu1 service retired), 3090 `.129:8000`
  (Windows `fbobe@`, watchdog scheduled-task **every 24h**, startup .bat +
  llama-watchdog.ps1), Jetson `.19:8080` (systemd, `/opt/models` NVMe).
- Rollback to 26B-A4B: `/data3/Models/gemma4_restore_config.txt`.

**Drydock fixes shipped tonight (v3.0.33→v3.0.38, all committed+pushed):**
- v3.0.33 timeout no longer mislabeled "Cannot reach the LLM" (600→1800s
  configurable via `request_timeout`, accurate timeout msg).
- v3.0.34 **vision input** — image path in a prompt → attached as multimodal
  block (`_user_content_with_images` in providers.py).
- v3.0.35 STOP kills the whole Bash **process tree** (start_new_session +
  `kill_process_group`/os.killpg) — not just the shell.
- v3.0.37 vision path-strip — strip backticks/parens/punct so a path in
  markdown ``code.png`` actually attaches (was silently missing → code-from-image fail).
- v3.0.38 pyright/CI green (mixed-value message dicts + None-guard).

**tbench harness is NOW WORKING** (`/data3/harbor_fork`, adapter
`.../agents/installed/drydock_agent.py`). It was blocked by broken container
networking — fixed: **ufw `DEFAULT_FORWARD_POLICY=ACCEPT`** + `systemctl
restart docker` + raw iptables `INPUT -s 172.16.0.0/12 -p tcp --dport 8899 -j
ACCEPT` (so per-task bridges can fetch the wheel). Cleaned 7 dead orphan containers.
- **Install spec (private repo → can't git-clone in container):** a **host
  wheel server** `python -m http.server 8899` over `/data3/drydock-v3/dist/`,
  set `DRYDOCK_INSTALL_SPEC=http://172.17.0.1:8899/drydock_cli-<ver>.whl`.
  Rebuild after code change: `pip wheel . --no-deps -w dist/`.
- **Backend pool env:** `DRYDOCK_BACKEND_POOL="http://192.0.2.10:8000/v1,
  ...x3 3090, x2 romulus .21, x2 remus .22, x1 jetson .19:8080"`.
- Launch: `harbor run --path tasks/terminal-bench-2 --agent drydock
  --n-concurrent 5 --n-attempts N --agent-timeout-multiplier 4 -i <task>...`

**Baselines / results:**
- v2 (mistral fork)+26B leaderboard: **15/47 = 31.9%** passed (opencode target 51.7%).
- EASY5 @1att (v3.0.36): 2/5 (headless-terminal ✓, multi-source ✓; db-wal,
  sqlite-with-gcov, code-from-image ✗). code-from-image fail = the vision
  path bug → fixed in v3.0.37.
- IN PROGRESS: EASY5 @3att on v3.0.37 (vision fixed); then the OVERNIGHT PLAN
  in `/tmp/overnight_tbench_plan.md`: v2-passed-10 @3att (does v3 hold v2's
  wins?) → frontier/untested @1att → compile v3+31B overall vs 31.9%/51.7%.
- Eval via `result.json` → `stats.evals[ev].reward_stats.reward` (1.0=pass);
  the per-trial `is_resolved` field is unreliable. Tally: `/tmp/tbench_worklist.md`.

**Open caveat:** 31B over-runs hard tasks (15–33min reasoning) → frontier
tasks mostly timeout-fail even at 4×; lever = lower server `--reasoning-budget`
(currently 20000), operator's call.
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

## Suggested next steps (pick up here)
- Operator review of the v3 hardening port.
- Phase-3 extras *only as real use justifies* (e.g. retrieval).
- PyPI/Docker republish once the suspended account is reinstated (appeal draft
  at `/data3/drydock/docs/pypi_reinstatement_appeal.md` — operator action).
- Remaining v2 ports are niche/N-A (v3's Edit uses old_string/new_string, not
  SEARCH/REPLACE markers, so the raw-marker fallbacks don't apply).
