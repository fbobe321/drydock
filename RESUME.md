# Drydock v3 — Session Resume / Handoff

Paste this as the first message of a new session to pick up where the last one
left off.

---

## What this is

Drydock v3 is a **clean-room, Apache-2.0, provider-agnostic terminal coding
agent** for local LLMs. It is NOT a fork — it replaces the v2 line (a
`mistral-vibe` fork whose inherited phone-home code got the PyPI account
quarantined). The whole point of v3 is clean IP provenance owned end to end.

- **Repo:** `https://github.com/fbobe321/drydock-v3` (PRIVATE), branch `master`.
- **Primary model:** Gemma-4-26B-A4B served by llama.cpp at
  `http://localhost:8000/v1` (model name `gemma4`). Uses harmony/gpt-oss
  `<|channel>` tokens, so it accepts `reasoning_effort`.

> The previous session ran on the workstation (`/data3/drydock-v3`, miniconda
> python, the model on local `:8000`). **This is a DIFFERENT machine** — see
> "Remote machine setup" below; ignore the workstation-only paths/flags noted
> later.

## Remote machine setup (you are here)

```bash
git clone https://github.com/fbobe321/drydock-v3.git
cd drydock-v3
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"        # openai, textual + pytest, ruff, pyright
```

**The model is the key dependency** — v3 only runs against an OpenAI-compatible
LLM endpoint. Editing + tests + lint work offline anywhere; *driving the TUI*
needs an endpoint. Pick one:

- **Tunnel to the workstation's model** (reuse the existing gemma4):
  ```bash
  ssh -N -L 8000:localhost:8000 <user>@<workstation-host>   # keep open
  drydock        # default base_url is localhost:8000 → now the tunnel
  ```
- **Point at any reachable endpoint:** `drydock --base-url http://HOST:PORT/v1 --model NAME`
  (or set `base_url`/`model` in `~/.drydock/config.toml`). Use `--provider
  ollama|lmstudio|vllm` for the known local servers.
- **Run a model locally on this box** (llama.cpp/Ollama/LM Studio); first launch
  autodetects it on :8000/:11434/:1234.

On this machine there's no v2 install, so `~/.drydock/` is v3's own — safe to
use directly (the HOME-isolation rule below was a workstation concern).

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

With the venv active (`pip install -e ".[dev]"` done), `drydock` and the tools
are on PATH:

```bash
drydock                       # run the TUI (in a project dir; needs an endpoint)
pytest tests/ -q              # full suite (177 tests)
ruff check drydock/ tests/
pyright drydock/              # deps are installed, so imports resolve
./scripts/release.sh          # build + security scan, stops before PyPI
```

(On the original workstation only: pytest needs `-o addopts= -p no:cov` to skip
a broken local pytest_cov plugin, and tools were invoked via
`/home/bobef/miniconda3/bin/python3`. A clean venv here needs neither.)

## NON-NEGOTIABLE working rules (learned the hard way)

1. **Verify harness-side fixes in a REAL tmux TUI session** — not just pytest.
   `tmux new-session -d -s v3 -x 200 -y 50 'cd <dir> && PYTHONPATH=/data3/drydock-v3 /home/bobef/miniconda3/bin/python3 -m drydock --model gemma4 --provider vllm'`,
   then `tmux send-keys` a real prompt, `tmux capture-pane -p` to read it, and
   run the built code functionally. `--help` is NOT a test.
2. **(Workstation-only) Isolate `HOME` for TUI tests** — there v3's
   `~/.drydock/` was shared with a running v2 install and a test once leaked
   `base_url` into it. On THIS machine there's no v2, so `~/.drydock/` is v3's
   own and you can use it directly. Still fine to sandbox with
   `HOME=/tmp/ddtest` if you want test runs kept out of your real config.
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

## Credentials / env
- **This machine:** use your own git auth to push (the repo is private under
  `fbobe321`). Python 3.12+ in the venv.
- **Workstation-only (ignore here):** GitHub token at
  `~/.config/drydock/github_token`, `gh` at `/home/bobef/miniconda3/bin/gh`,
  tbench notification pause flags at `/data3/drydock/.pause_tbench_*`, and the
  PyPI appeal draft at `/data3/drydock/docs/pypi_reinstatement_appeal.md`.

## Suggested next steps (pick up here)
- Operator review of the v3 hardening port.
- Phase-3 extras *only as real use justifies* (e.g. retrieval).
- PyPI/Docker republish once the suspended account is reinstated (appeal draft
  at `/data3/drydock/docs/pypi_reinstatement_appeal.md` — operator action).
- Remaining v2 ports are niche/N-A (v3's Edit uses old_string/new_string, not
  SEARCH/REPLACE markers, so the raw-marker fallbacks don't apply).
