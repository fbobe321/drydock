# DryDock — Local CLI Coding Agent

**Repository:** https://github.com/fbobe321/drydock
**PyPI:** https://pypi.org/project/drydock-cli/ (v2.6.107)
**License:** Apache 2.0 (fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe))

## Vision

Best-in-class local coding agent. Build, debug, and ship software using local
LLMs on consumer hardware — and **prove it actually works** with a test
harness that drives the real TUI like a real user, not a tool-call counter.

## Current Status

- **Active model:** Gemma 4 26B-A4B-it-AWQ-4bit (MoE, 4B active params per
  token, ~70 tok/s) via vLLM Docker on 2x RTX 4060 Ti 16GB
- **Version:** 2.6.107 on PyPI. The v2.6.79–v2.6.107 wave shipped 28+
  loop-prevention iterations: Read-before-Write contract, read dedup,
  terse results, token sanitization, system-reminder framing, JSON
  sanitizer, field-aware path cleaner, fake-tool-call text detection,
  thinking-channel leak strippers, oscillation/path-dominance/error-
  storm/cancellation detectors, exact-call circuit breaker, sticky-
  flag fixes, MessageList.pop, inline stall retry, line-break
  paragraph fix, per-prompt 15-min budget + 35-call ceiling, Gemma 4
  80K compact threshold, and finally the **session-reset-every-10-
  prompts pattern** (v2.6.107) borrowed from
  [Adversarial Code Review](https://asdlc.io/patterns/adversarial-code-review/).
- **Users can:** build projects from PRDs, fix bugs, review code, refactor
  — through the TUI only (headless mode is gone)

## Stress Test Progress (200-prompt benchmark)

The 201-prompt stress test (`scripts/stress_shakedown.py` →
`scripts/stress_prompts_tool_agent.txt`) drives the real drydock TUI
through 1 build + 200 feature additions on `tool_agent`, treating
context bloat, attractor loops, model degeneration, and rendering as
a unified end-to-end test.

### Purpose

**The point of the stress test is to improve the TUI experience for
the actual end user.** The user does NOT use the harness — the harness
is a test driver, not a product. Every fix should land in drydock
itself (or in the model-handling layer) so a real user typing into the
TUI sees the improvement.

Rules of engagement:

- **Drydock fixes are the goal.** When the harness exposes a regression
  (loops, hangs, silent prompts, slow responses), the fix goes in
  `drydock/` and ships in a new release. The harness numbers are a
  proxy for user experience; never optimise harness numbers in a way
  that doesn't help the user.
- **Harness changes are ONLY to keep the harness functional with the
  TUI.** If pexpect can't drive the new TUI, fix pexpect glue. If the
  trust dialog blocks input, auto-dismiss it. If the session log path
  changes, update the watcher. **Do not tweak harness parameters
  (`SESSION_RESET_EVERY`, idle timeouts, retry counts) to inflate step
  throughput.** That's gaming the metric, not improving drydock.
- The hour-by-hour step count is signal, not the goal. The goal is
  drydock that doesn't loop, doesn't hang, doesn't go silent, and
  produces working code on long sessions for a real user.

**Best run so far:** v2.6.102 reached 96/201 prompts before tool calls
started returning `<user_cancellation>` infinite loop.

**Current run:** v2.6.107 with session-reset-every-10 pattern. Cron
job sends hourly status to Telegram (`scripts/stress_telegram_status.py`).
If a run completes 201 prompts, the goal extends to 2000 prompts.

The pattern of failure modes hit (and shipped fixes for):
- Tight identical-call loops (v2.6.79–v2.6.95)
- Multi-variant oscillation on same path (v2.6.87–v2.6.90)
- Path corruption with leaked tokens (v2.6.88)
- Fake-tool-call text without real tool_calls (v2.6.91)
- Empty-response stalls (v2.6.99)
- MessageList.pop crash silently swallowed (v2.6.98)
- Sticky `_loop_detected` baking `frequency_penalty` into all output
  → suppressed SPACE token → "no spaces" (v2.6.100, v2.6.105)
- Cancellation infinite loops (v2.6.106)
- Context bloat past 80K → vLLM hangs (v2.6.103)
- TUI line-break rendering (v2.6.101, v2.6.104)
- Per-prompt no-end-condition (v2.6.101 — 15-min + 35-call budget)
- All of the above prevented up-front by **session-reset every 10
  prompts** (v2.6.107) — bounded context, no rot

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

For longer-form regression (what the single-prompt suite cannot catch):

```bash
# 24-step interactive conversation per medium-hard PRD (30+ min each)
python3 scripts/shakedown_interactive.py \
    --cwd /data3/drydock_test_projects/403_tool_agent \
    --pkg tool_agent
```

The single-prompt suite catches block-loops, hallucinated tools, and
first-turn dead silence. Interactive shakedown exposes slow-drift
oscillation, thinking-stall between file writes, search_replace
cascades that only appear after several edits, and the cost of
echoing back content the model already wrote. Both are needed.

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

### Safety — advisory only, never blocks the TUI
After a regression in v2.6.79/v2.6.80 where hard `ToolError` blocks
caused the tool to refuse legitimate retries and spin the TUI in its
own block-loop, all loop-breakers are now structured returns with
guidance messages. Nothing here can stop the session. The full rule
is codified in memory as
`feedback_no_tool_errors_for_loop_detection.md`.

- Tool permission system (always/ask/never per tool)
- Loop detection wired into `_handle_tool_response()` — injects an
  advisory nudge when the same tool call repeats, never stops the
  session
- **Read-before-Write / Read-before-Edit** (Claude Code tool contract).
  `write_file` and `search_replace` on an existing file the model has
  NOT read this session return a structured no-op with a
  `<system-reminder>` telling it to `read_file` first. After a
  successful write or read, the session's `read_file_state` dict is
  updated so chained operations pass without a re-read. mtime-based
  staleness check also triggers the advisory when the file changed
  on disk between the read and the write.
- **Read dedup stub** — `read_file` on a path+offset+limit with
  unchanged mtime since the earlier read in this session returns a
  `<system-reminder>` pointing to the earlier tool_result instead of
  burning context re-reading.
- **Dedup advisories** on `write_file` — escalating text if the model
  sends identical content to an existing file, including directory
  listing and per-path write count. All advisory, no ToolError.
- **Syntax-error context** — when a Python write fails `ast.parse`, the
  warning embeds ±3 surrounding source lines so the model can see the
  structural mistake, not just the error message. Consecutive-error
  counter escalates guidance at 2+ and 3+ but never blocks.
- **`<system-reminder>` framing** on every high-signal advisory — the
  model attends to these more consistently than plain result text.
- **Centralized `safe_parse_tool_args`** — handles Gemma-4 leaked
  thinking-token markers (`<|channel>...`, `<|tool_call>...`, orphan
  `\Fix`-style escapes that abort the stdlib JSON decoder). Used at
  every tool-arg JSON decode site so no call path can crash on leaked
  escapes. Falls through to `{"_parse_error": "..."}` as the last
  resort.
- **Terse tool results** (Claude Code pattern). `write_file` returns
  just `"File X updated successfully (N bytes)."` — no echo of
  `args.content`. A 3KB write no longer adds 3KB of redundant context.
  Prevents the "model re-reads what it just wrote" class of loops.
- **Token-level loop-breaker** in agent_loop — temperature bump +
  `frequency_penalty` + `presence_penalty` + fresh seed when
  `_check_tool_call_repetition()` detects repeat signatures. Heavier
  bumps at FORCE_STOP threshold.
- **Gemma-4 auto-disabled tools** — `task` (subagent delegation),
  `task_create`, `task_update`, `task_list`, `ask_user_question`,
  `invoke_skill`, `tool_search`. Model mixed `task` up with `todo`
  and the subagent response leaked raw thinking tokens that hung the
  parent session. Filtered out in `ToolManager.available_tools` when
  the active model name contains "gemma".
- **`_check_main_module_entry()`** catches `__main__.py` files that
  import `main` but never call it (the codec silent-exit bug)
- **`_check_missing_sibling_imports()`** catches `from .x import Y`
  when `x.py` doesn't exist on disk yet (the minivc unimportable-
  package bug)
- **`_check_stub_classes()`** catches the lang_interp anti-pattern
  where the model writes `class Interpreter: pass` inline in cli.py
  to silence ModuleNotFoundError instead of writing the real module
- **`_check_bare_raise_outside_except()`** — surfaces bare `raise`
  in function bodies outside an except handler (real bug from the
  ACE v2 build)
- API error auto-recovery (retry after 10s)
- `auto_release.sh` and `watchdog.sh` cron jobs respect pause flags
  at `.pause_auto_release` and `.pause_watchdog` for manual debugging

### Read-only bash auto-accept
The bash tool's default allowlist now covers most read-only commands
without requiring per-user configuration:

- File/dir inspection: `ls`, `cat`, `head`, `tail`, `wc`, `file`,
  `stat`, `pwd`, `which`, `basename`, `dirname`, `realpath`, `readlink`
- Search: `grep`, `rg`, `fd`, `fdfind`, `ag`, `find`, `tree`
- Text processing: `diff`, `cmp`, `sort`, `uniq`, `awk`, `cut`, `tr`
- Git read-only: `git diff`, `git log`, `git status`, `git show`,
  `git branch`, `git ls-files`, `git grep`, `git remote`,
  `git rev-parse`, `git blame`, `git config --get`, `git tag`,
  `git stash list`
- System info: `date`, `id`, `hostname`, `env`, `printenv`, `du`,
  `df`, `ps`, `free`, `uptime`, `uname`, `whoami`
- Python dev: `pip install/list/show/freeze/check`, `conda
  list/info/env list`, `python -c`, `python -m pip`, `pytest`, `make`,
  `tox`

Bash commands are AST-split so pipes/chains are each checked
individually — `ls | rm` still blocks on the `rm`.

### Config debt management
Package defaults ship in code, but user `~/.drydock/config.toml` is
mutable. When `pip install -U drydock-cli` adds new defaults (new
allowlist entries, etc.), users who'd customized the field previously
lost the new additions. Fixed two ways:

- **Option A — auto-merge** (`bash.allowlist`, `bash.denylist`,
  `bash.denylist_standalone`): user values are UNIONED with package
  defaults at load time, so new package defaults auto-propagate.
  Escape hatch: include `"__override__"` in the list to use it
  verbatim and skip defaults.
- **Option C — `drydock --doctor`** command shows drift vs. current
  defaults in a rich table. `drydock --doctor --fix` rewrites
  `config.toml` to union missing defaults with a `.bak` backup.
  Verified end-to-end on the project maintainer's config: 51 missing
  defaults detected, applied, re-run reports clean.

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

## Recent fixes (April 13–15, 2026)

### v2.6.107 (April 15) — session-reset every 10 prompts in stress harness
Adversarial-code-review pattern from asdlc.io: prevent context rot
by SESSION SEPARATION, not reactive compaction. Stress harness now
sends `/clear` every 10 user prompts and a one-line state preamble.
Each batch starts with bounded context, sidestepping all the
context-bloat symptoms we'd been patching individually.

### v2.6.106 (April 15) — record cancelled tool calls in circuit breaker
`asyncio.CancelledError` handler ran `_handle_tool_response` but not
`_circuit_breaker_record`, so cancelled calls didn't increment the
counter. Stress hit 15+ identical read_file all returning
`<user_cancellation>`, breaker never fired. Now all cancellations
count toward the 12-call threshold.

### v2.6.105 (April 15) — clear `_loop_detected` after sampling — fixes "no spaces in TUI"
The `frequency_penalty=0.4` (WARNING) / `0.7` (FORCE_STOP) loop-break
sampling was sticking on across turns. v2.6.100 only cleared the flag
in the FORCE_STOP `tool_choice="none"` path; the sampling-only path
left the flag set. `_check_tool_call_repetition` only updates the
flag on tool-result handling, so text-only turns never reset it.
Result: `frequency_penalty` baked into every subsequent generation
suppressed the SPACE token (most-repeated). Model emitted
"Iwillnowadd" instead of "I will now add". User-visible "no spaces
in TUI text" was caused here. Fix: clear `_loop_detected` and
`_loop_signal` in BOTH consumption paths.

### v2.6.104 (April 15) — line-break paragraph fix
Replaced trailing-spaces hard-break (`  \n`) — which Textual rendered
as visible double-spaces — with paragraph break (`\n\n`). Skips list
items and code fences.

### v2.6.103 (April 15) — Gemma 4 compact threshold cap at 80K
Default `auto_compact_threshold` was 200K; Gemma 4 max context is
131K. Auto-compact never fired. Per-model cap at 80K for any model
whose name contains "gemma" leaves headroom for response.

### v2.6.102 (April 15) — exact-call circuit breaker re-enabled
Stress hit 91× identical search_replace with same content. Re-enabled
`_circuit_breaker_check` (was hardcoded `return None`) with high
thresholds: 8 for write/edit/bash, 12 for read-only. Returns NOTE
result; 5 consecutive breaker fires → forced session stop.

### v2.6.101 (April 15) — 15-min per-prompt budget + line-break preserve
Per-user-prompt wall-clock budget (15 min) and tool-call ceiling (35).
After either limit, drydock yields a clean assistant message and ends
the turn. Returns control to the user. Also added `_preserve_line_breaks`
(replaced in v2.6.104).

### v2.6.100 (April 15) — clear `_loop_detected` after FORCE_STOP
First fix for the sticky-flag bug (incomplete — also needed v2.6.105).

### v2.6.99 (April 15) — inline stall-retry for empty responses
When the model emits empty (no content + no tool calls), retry the
LLM call inline up to 3 times within the same user turn, popping
the empty assistant and injecting an escalating nudge each retry.

### v2.6.98 (April 15) — MessageList.pop method
v2.6.96/97 empty-nudge was crashing silently every fire because
MessageList is a custom Sequence and has no `pop()` method. Added
`pop(index=-1)` mirroring list.pop.

### v2.6.97 (April 15) — narrowed thought-nuker + per-user-msg counter reset
v2.6.96 was over-aggressive (matched any "thought" prefix). Narrowed
to `^thought\s*/` or `^thought\s*\n` only. Counter resets when a new
user message arrives.

### v2.6.96 (April 15) — nuke `^thought` thinking-channel leaks
Gemma 4 emits "thought / The user wants to add X" narrative without
calling tools. Detect and nuke to None so empty-nudge fires.

### v2.6.95 (April 15) — same-tool-repeat + error-storm detectors
Check 1a: same tool name 8+ consecutive (regardless of args) →
FORCE_STOP. Check 1c: ≥8 of last 10 same tool AND ≥6 errors →
FORCE_STOP. Both feed per-tool mute.

### v2.6.94 (April 15) — task tool re-enabled + write metric for bash heredoc
Task subagent re-enabled now that v2.6.91's sanitization handles its
output. Stress harness counts `cat <<EOF > file` as a write so the
metric stops false-zeroing when model pivots to bash file creation.

### v2.6.93 (April 15) — entrypoint.py duplicate import hotfix
v2.6.83 had a local import inside `--doctor` branch that shadowed
the module-level binding → `UnboundLocalError` on every non-doctor
invocation. Broke drydock entirely. Fixed.

### v2.6.92 (April 15) — bash command in path-dominance + empty-nudge widening
Path-dominance check now includes `command` field for bash. Empty-
nudge widened to fire on user→empty too (was only tool→empty).

### v2.6.91 (April 14) — fake-tool-call text detection
Gemma 4 sometimes degenerates into emitting `<|tool_call>call:...{`
as plain text instead of real tool_calls. Detect this shape and nuke
content to None so empty-nudge fires.

### v2.6.90 (April 14) — per-tool mute on path-dominance
Replaced blunt `tool_choice="none"` with surgical per-tool removal:
when 9/12 calls touch same path, remove that specific tool from
`available_tools` for one turn. Model must diversify.

### v2.6.89 (April 14) — relaxed oscillation detection
≥9 of last 12 calls touch same path → FORCE_STOP, regardless of
signature variance.

### v2.6.88 (April 14) — field-aware path cleaner
Strip leak tokens AND orphan backslashes before letters from path-
like fields (`path`, `file_path`, `command`, `cwd`, `url`).
Preserve content fields untouched (regex strings need `\d` etc.).

### v2.6.87 (April 14) — multi-variant oscillation detector
Last 12 calls have ≤3 distinct sigs AND ≥9 touch single path →
FORCE_STOP.

### v2.6.86 (April 14) — `tool_choice="none"` loop-break + harness pacing
First version of FORCE_STOP→tool_choice="none" pattern (later
replaced by per-tool mute in v2.6.90).

### v2.6.85 (April 14) — centralized JSON sanitizer + terse tool results
`safe_parse_tool_args` helper; write_file/search_replace return
terse `"X updated successfully (N bytes)"` instead of echoing content.

### v2.6.84 (April 14) — entrypoint UnboundLocalError hotfix
(See v2.6.93 for the same bug class — different version.)

### v2.6.83 (April 14) — Claude Code tool contract patterns
Read-before-Write/Edit, read dedup stub, system-reminder framing,
read-only bash auto-accept, config Option A + C.

### v2.6.79–v2.6.82 (April 13–14) — initial loop-breaker iterations
Syntax-thrash, cumulative path-write, Gemma 4 auto-disable, leak-
token stripping. Some patterns later reverted to advisory-only when
hard blocks caused worse loops.

### v2.6.79 (April 13) — syntax-thrash loop-breaker
Added ToolError on 3rd consecutive syntax-error write to same file.
Caught color_converter's 38-consecutive-bad-writes case in a single
shakedown run.

### v2.6.80 (April 14) — cumulative path-write gate
Added ToolError at 5 writes to the same path regardless of content,
to catch the write_file ↔ search_replace ping-pong pattern in minivc
(model wrote `commands/__init__.py` 11 times, each clobbering a
search_replace fix).

### v2.6.81 (April 14) — Gemma 4 auto-disable + leak-token stripping
- `ToolManager.available_tools` now filters `task`/`todo`-family tools
  when the active model name matches "gemma" — Gemma 4 confuses
  `task` (subagent) with `todo` and the subagent response leaks raw
  `<|tool_call>` markers that hang the parent session.
- Broadened thinking-token stripping in `process_api_response_message`:
  handles bare `<|tool_call>...<tool_call|>`, stray `<|"|>`, unpaired
  `<|...|>` from stream truncation, and nukes content to `None` when
  the residue is just `call:toolname{...}` garbage so the agent
  loop's empty-content nudge can fire.

### v2.6.82 (April 14) — REVERT all hard blocks to advisory
User reported: "it is trying to write a file or update something,
blocked... these loops make the tool unusable... little short tasks
you don't see it. Do anything that takes a while, many steps, and
it can't get through it."

The v2.6.79/v2.6.80 ToolErrors were making long tasks worse — model
got BLOCKED on one write, panicked, retried the same write, got
BLOCKED again, spun until timeout. Per the existing CLAUDE.md rule
"safety mechanisms must be advisory not blocking," reverted all
loop-breaker ToolErrors to structured WriteFileResult /
SearchReplaceResult / BashResult with strong guidance text. Memory
updated: `feedback_no_tool_errors_for_loop_detection.md`.

Also shipped in v2.6.82:
- `safe_parse_tool_args` sanitizer for `tool_call.arguments` —
  Gemma-4 leak tokens inside JSON strings (e.g. `"<|\Fix"`) make
  `\F` an invalid JSON escape and crash the decoder.
- TUI reasoning widget no longer finalizes on `ToolCallEvent` — a
  turn with N tool calls was producing N stacked "Thought" widgets.
  Now one "Thought" spans the full turn.

### v2.6.83 (April 14) — Claude Code tool contract
Second review of `/data3/claude-code/src` produced the real loop
fixes. Borrowed patterns (ideas only, no code copied):

- `read_file_state` dict on `AgentLoop`, passed through `InvokeContext`
  to every tool. Session-scoped map of path → `{content, timestamp,
  offset, limit}`.
- Read-before-Write in `write_file`: overwriting an existing unread
  file yields a structured no-op with a `<system-reminder>` telling
  the model to `read_file` first.
- Read-before-Edit in `search_replace`: same contract for SR.
- Read dedup stub in `read_file`: same path+offset+limit with
  unchanged mtime returns `<system-reminder>` pointing to earlier
  tool_result instead of re-reading.
- `<system-reminder>` framing on dedup/syntax/oscillation advisories.
- Read-only bash auto-accept: 30+ new safe commands in the default
  allowlist (`rg`, `grep`, `fd`, `git show`/`branch`/`ls-files`, etc.).
- Config A + C: `bash.allowlist`/`denylist` auto-merge user values
  with package defaults on load; `drydock --doctor [--fix]` command
  reports and repairs config drift.

### v2.6.84 (April 14) — entrypoint hotfix
My duplicate `from ... import init_harness_files_manager` inside the
`--doctor` branch in `entrypoint.py` shadowed the module-level import
as a local variable, triggering `UnboundLocalError` on every
non-doctor drydock invocation. Broke v2.6.83 completely. Fixed by
dropping the redundant import.

The tail-end of the v2.6.83 shakedown suite (`csv_sorter`,
`makefile_gen`, `json_pipeline`) all showed 7-second 0-message
sessions — all of those were this crash, not real loop regressions.

### v2.6.85 (April 14) — terse results + centralized sanitizer
Second claude-code pass produced two more wins:

- Centralized `safe_parse_tool_args` helper — used in
  `format.parse_message` AND `anthropic._convert_tool_call` (the
  Anthropic backend previously had bare `json.loads` that could
  crash on the same leak-token class of bugs).
- Terse tool-result content: `write_file` used to echo the full
  `args.content` back in `result.content` — every successful 3KB
  write added 3KB of redundant context, and the model re-reading
  that blob was a documented oscillation trigger. Now returns
  `"File X updated successfully (N bytes)."` Similar for
  `search_replace`.

### Earlier (v2.5.x–v2.6.78)
- `slim_system_prompt` config knob — dropped first-turn latency on
  Gemma 4 from 60-120s to 10-20s
- Trust dialog auto-dismissal in shakedown.py
- PRD reset between shakedown runs with PRD.master.md canary
- `_task_manager.py` rename (underscore prefix excludes from tool
  discovery)
- `auto_release.sh` fail-loud on token errors
- `_check_tool_call_repetition` wired into `_handle_tool_response`
- `_truncate_old_tool_results` proactive shrinkage

## Roadmap

### Currently in flight (April 15, 2026)
- **200-prompt stress test** running against v2.6.107 (PID
  1614232 as of writing). Hourly Telegram status via cron. If the
  current run completes 201 prompts cleanly, goal extends to 2000.
- **Telegram notifications**: `scripts/stress_telegram_status.py`
  fires every hour via crontab; reports prompt progress, session
  health, dup-ratio, max-consecutive, error count.

### Likely to retire after v2.6.107 proves out
With session-reset every 10 prompts, several earlier safety nets
become defense-in-depth instead of load-bearing. Candidates to
simplify or remove if a few clean stress runs land:
- Some of the cascade of FORCE_STOP detectors (Check 1a/1b/1c) —
  most won't trigger at all in 10-prompt batches
- The 35-call per-prompt ceiling — model usually finishes faster
  than that within a batch
- The 80K compact threshold — context shouldn't approach it any more

### Near-term backlog (still relevant)
1. **Stop-sequences for leak tokens** — add `<|channel>`,
   `<|tool_call>`, `<|"|>` as vLLM stop-sequences so generation halts
   before a leaked marker lands in the stream. Kills a class of
   JSON-decode + rendering issues at the tokenizer.
2. **Per-turn status `<system-reminder>`** — "tool calls: 8/35 |
   writes-per-file: cli.py=6". Injected every N turns to give the
   model raw awareness within a batch.
3. **ruff inline on writes** — run `ruff check` on every Python write
   and feed errors back as `<system-reminder>` warnings (cheaper than
   full LSP).
4. **Grammar-constrained tool args** via vLLM's lm-format-enforcer —
   JSON schema per tool; tokenizer can't emit invalid JSON. Kills the
   `\Fix`-class of bugs entirely at the generation level.
5. **LSP integration** (pyright/ruff) — real diagnostics after every
   write. Large lift but highest feedback quality.
6. **Time-based microcompact** — replace old `tool_result` content
   with a stub like `[Old tool result content cleared]` after N
   minutes/turns. Claude Code pattern; drydock has per-call truncation
   but not time-based aging. With session-reset, may not even matter.

### Adversarial-review pattern (next big architectural move)
Source: [asdlc.io / Adversarial Code Review](https://asdlc.io/patterns/adversarial-code-review/).
The next architectural improvement after session-reset:

- **Builder/Critic split**: after a builder agent finishes a feature,
  spawn a CRITIC agent in a fresh session whose ONLY job is to verify
  the diff against a spec file. Critic returns PASS or numbered
  violations. Builder iterates on violations. Echoes the
  separation-of-concerns we got from session-reset — applies it at
  the verification level too.
- **Spec file as binding contract**: blueprint + constraints +
  anti-patterns. Currently AGENTS.md plays this role; could promote
  to a structured PRD-with-acceptance-criteria format.

### Medium-term
- **Second deployment target** so PyPI failures don't lose history
- **Replace `tui_test.py` and `core_tests_real.sh` entirely** —
  `shakedown.py` + `shakedown_interactive.py` + `stress_shakedown.py`
  are the only honest tests
- **Token cost dashboard** so first-turn prefill regressions catch
  before they hit users
- **Consultant escalation** — partly wired in `agent_loop.py`; finish
  the hook so on loop-depth ≥ N a bigger model gets ONE turn of
  advice and returns control to Gemma

### Long-term
- Support larger models as hardware improves
- Plugin marketplace for custom tools/skills
- Web dashboard for monitoring long runs (replace the cron+Telegram
  status with a real timeline)
- Multi-model routing (different models for different tasks)
- Fine-tune Gemma 4 on bail-out traces (sessions where drydock
  recovered from a loop). Pairs naturally with the user's "Deep Noir"
  research direction.
- 13-day autonomous run goal (per industry reports of
  spec-driven + adversarial-review agents reaching that horizon)

## Architecture Notes

See [CLAUDE.md](CLAUDE.md) for technical details, file locations,
constraints, lessons learned, and the development workflow including
the `.pause_auto_release` / `.pause_watchdog` flags you'll want during
manual debugging.

## Legal

All code is original or forked from Apache 2.0 mistral-vibe.
Architecture improvements are standard design patterns implemented from
scratch. No proprietary code copied.
