# DryDock — Local CLI Coding Agent

**Repository:** https://github.com/fbobe321/drydock
**PyPI:** https://pypi.org/project/drydock-cli/ (v2.6.25)
**License:** Apache 2.0 (fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe))

## Vision

Best-in-class local coding agent. Build, debug, and ship software using local
LLMs on consumer hardware — and **prove it actually works** with a test
harness that drives the real TUI like a real user, not a tool-call counter.

## Current Status

- **Active model:** Gemma 4 26B-A4B-it-AWQ-4bit (MoE, 4B active params per
  token, ~70 tok/s) via vLLM Docker on 2x RTX 4060 Ti 16GB
- **Version:** 2.6.25 on PyPI (2.6.26+ in source with `slim_system_prompt`,
  hard-block on duplicate writes, and the new test harness)
- **Honest test results (10 core PRDs through `shakedown.py`, single run):**
  5/10 PASS without slim_system_prompt + hard block; ≥8/10 expected with
  both fixes deployed (variance run in progress)
- **Users can:** build projects from PRDs, fix bugs, review code, refactor
  — through the TUI only (headless mode is gone)

## How drydock is tested now

The `scripts/tui_test.py` and `core_tests_real.sh` harnesses count tool
calls and `--help` exit codes. They reported 80% pass rates while real
users saw drydock loop, hang, and produce broken code. **Both are
deprecated** in favour of `scripts/shakedown.py`, which:

- Drives the real `drydock` TUI via pexpect (no headless code paths)
- Polls the live `~/.vibe/logs/session/session_<id>/messages.jsonl` in
  parallel to see what the model is actually doing
- Watches for **write loops** (≥3 identical-content writes to a path),
  **hallucinated tool names** that hang the session, **search_replace
  failure cascades**, and **dead silence** (no new messages of any kind
  for 120 seconds)
- Types simulated `STOP` interrupts when it detects loops and tracks
  whether the model OBEYS them
- Distinguishes three end-states: active turn, model declared done
  (text response with no tool call), and dead silence
- Resets the cwd between runs (restores `PRD.md` from `PRD.master.md`,
  wipes the package dir and stale data dirs) so contamination from one
  run can't poison the next
- Auto-handles the "Trust this folder?" dialog drydock pops on new
  directories
- **Pass criteria are user-perceptible**: no loops, no ignored
  interrupts, no search_replace cascades, the package must actually
  execute, session must finish under the time budget

Run a single project:

```bash
PYTHONUNBUFFERED=1 python3 -u scripts/shakedown.py \
    --cwd /data3/test_drydock \
    --prompt "review the PRD and get started" \
    --pkg doc_qa_system
```

Run the 10-project core suite (or 3-run variance suite for stability):

```bash
bash scripts/shakedown_suite.sh        # one run, ~25 min
bash scripts/shakedown_variance.sh 3   # three runs, ~75 min
```

## Features

### Core
- 24 builtin tools (bash, grep, read_file, write_file, search_replace,
  glob, todo, ask_user_question, invoke_skill, worktree, etc.)
- 7 bundled skills (/investigate, /review, /ship, /batch, /simplify,
  /deep-research, /create-presentation)
- Multi-agent delegation (explore, diagnostic, planner subagents)
- Plan→Edit workflow (Mistral's designed pattern)
- AGENTS.md support (cross-tool standard, auto-created — same file works
  with Claude Code, opencode, drydock, Aider)
- Textual TUI with wave spinner, message queuing

### Local Model Support
- `/setup-model` command (vLLM, Ollama, LM Studio, custom)
- `--local` CLI flag for quick setup
- Auto-detect model name from server
- **`slim_system_prompt`** config knob — drops the ~10K-token inlined
  tool prompt files + skills + subagents lists for local models that
  pay 7-12 ms per prefill token. Verified on Gemma 4: takes
  first-turn latency from 60-120 s to 10-20 s on the dead-silence cases.

### Safety (advisory, never blocking — with one narrow exception)
- Tool permission system (always/ask/never per tool)
- Loop detection wired into `_handle_tool_response()` — injects an
  advisory nudge when the same tool call repeats, never stops the
  session
- Three-tier escalating dedup on `write_file`:
  1. First duplicate: friendly "move to next file"
  2. Second duplicate: full directory listing + concrete next-action
     suggestions
  3. **Third duplicate: HARD BLOCK via `ToolError`** — narrow exception
     to the no-blocking rule, only fires when file exists AND content
     is identical AND it's the 3rd attempt. Never blocks legitimate
     retries. After the block, `_prune_duplicate_writes()` removes the
     older no-op write attempts from message history so the next turn
     sees a cleaner context.
- `_check_main_module_entry()` catches `__main__.py` files that import
  `main` but never call it (the codec silent-exit bug)
- `_check_missing_sibling_imports()` catches `from .x import Y` when
  `x.py` doesn't exist on disk yet (the minivc unimportable-package bug)
- `_truncate_old_tool_results()` proactively shrinks stale `read_file`
  outputs in message history before each LLM call (idempotent;
  keeps the last 6 in full)
- API error auto-recovery (retry after 10s)
- `auto_release.sh` and `watchdog.sh` cron jobs respect pause flags
  at `.pause_auto_release` and `.pause_watchdog` for manual debugging

## Install

```bash
pip install drydock-cli
drydock --local http://localhost:8000/v1
```

For Gemma 4 / vLLM users, add this to `~/.drydock/config.toml` to drop
the system prompt bloat:

```toml
slim_system_prompt = true
```

## Per-Project Instructions

DryDock loads instructions from these files in the project root, in
order:

- **AGENTS.md** — Cross-tool standard (recommended). Same file works
  with Claude Code, opencode, drydock, Aider, and other coding agents.
- **DRYDOCK.md** — Legacy / drydock-only. Kept for backward compat;
  prefer AGENTS.md for new projects.
- **.drydock/rules/*.md** — Modular rules

Auto-created if none exist. AGENTS.md is essential for devstral
(it loops without one) but Gemma 4 works without it.

## Recent fixes (April 2026)

The user-pain debugging arc surfaced a string of issues that the old
test harnesses couldn't catch. All committed in source and live in
PyPI v2.6.25 (or queued for v2.6.26):

- **Hard-block duplicate writes after 3 attempts** — narrow exception
  to "no blocking" for pure no-op work. Verified: codec went from
  FAIL (4× `__init__.py` loop, ignored STOP interrupt, broken --help)
  → PASS (131 s, 10 writes, no loops, --help works).
- **`slim_system_prompt`** — drops the inlined tool prompt files +
  skills + subagents lists from the system prompt (~10K → ~3K tokens
  for Gemma 4). Fixed the dead-silence-on-first-turn issue. Verified:
  todo_list FAIL (0 messages in 121 s) → PASS (38 messages in 115 s).
- **Trust dialog auto-dismissal in shakedown.py** — drydock pops a
  blocking "Trust this folder?" modal on unfamiliar directories. The
  harness now detects and answers it.
- **PRD reset between runs** — the model edits PRD.md across sessions
  (adds "✅ Completed" tables, chat-style filler). The harness restores
  from `PRD.master.md` automatically, with the canary fixture at
  `tests/fixtures/doc_qa_system_prd.md`.
- **`_task_manager.py` rename** — TaskCreate/Update/List were a duplicate
  task system that confused Gemma 4 into hanging. Underscore prefix
  excludes them from tool discovery.
- **`auto_release.sh` fail-loud on token errors** — the GitHub deploy
  step was using `2>/dev/null` so an expired token silently skipped the
  push. The remote was 287 commits behind for ~2 days before anyone
  noticed.
- **Loop detection wired up** — `_check_tool_call_repetition()` was
  defined but never actually called from anywhere. Now fires from
  `_handle_tool_response()` and injects an advisory nudge.
- **`_truncate_old_tool_results()`** — proactive shrinkage of stale
  `read_file` outputs to keep context small as sessions grow.

## Roadmap

### Near-term
- Confirm 8-10/10 stable pass rate on the 10-project shakedown suite
  with `slim_system_prompt` + hard block (variance run in progress)
- Per-model `auto_compact_threshold` (current default of 200K is
  higher than Gemma 4's 131K max context, so it never fires)
- Streamline the system prompt further: drop the universal sections
  that don't apply to local-model agents
- Investigate why some test_projects PRDs pass on one run and loop on
  the next — variance is the rule with Gemma 4

### Medium-term
- Per-model knobs: `slim_system_prompt`, `thinking`, tool exclusion
  list, `auto_compact_threshold`
- A second deployment target so PyPI failures don't lose history
- Replace the old `tui_test.py` and `core_tests_real.sh` entirely;
  shakedown.py is the only honest test
- Token cost dashboard so first-turn prefill regressions are caught
  before they hit users

### Long-term
- Support larger models as hardware improves
- Plugin marketplace for custom tools/skills
- Web dashboard for monitoring long runs
- Multi-model routing (different models for different tasks)

## Architecture Notes

See [CLAUDE.md](CLAUDE.md) for technical details, file locations,
constraints, lessons learned, and the development workflow including
the `.pause_auto_release` / `.pause_watchdog` flags you'll want during
manual debugging.

## Legal

All code is original or forked from Apache 2.0 mistral-vibe.
Architecture improvements are standard design patterns implemented from
scratch. No proprietary code copied.
