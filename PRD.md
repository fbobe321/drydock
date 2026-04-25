# DryDock — Local CLI Coding Agent

**Repository:** https://github.com/fbobe321/drydock
**PyPI:** https://pypi.org/project/drydock-cli/ (v2.7.4)
**License:** Apache 2.0 (fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe))

## Vision

Best-in-class local coding agent. Build, debug, and ship software using local
LLMs on consumer hardware — and **prove it actually works** with a test
harness that drives the real TUI like a real user, not a tool-call counter.

Drydock is a **living harness that adapts to tasks and models**. Admiral
(in-process supervisor) watches each session and applies detector-driven
interventions; the Meta-Harness experimenter drives an overnight auto-tuning
loop against the stress test and proposes configuration or prompt mutations
from trace data. Both run with a local-LLM-first, air-gapped posture —
proposers and analyzers default to the operator's own vLLM instance, and
cloud escalation is gated behind an explicit env flag.

## Current Status

- **Active model:** Gemma 4 26B-A4B-it-AWQ-4bit (MoE, 4B active params per
  token, ~70 tok/s) via vLLM Docker on 2x RTX 4060 Ti 16GB
- **Version:** 2.7.4 on PyPI. The v2.6.79–v2.6.107 wave shipped 28+
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
  The v2.6.108–v2.7.4 wave added **Admiral** (in-process supervisor,
  Phase 1→3b + empty-after-tool and retry-after-error detectors), the
  **Meta-Harness experimenter** (Karpathy-style overnight auto-tuning
  with airgap-locked proposer), and a batch of rendering + tool-call
  serialization fixes from user-reported GitHub issues #8–#13.
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

**Current focus:** The stress test is the historical benchmark, but
active development has shifted to Admiral (in-process supervisor that
actuates on detectors without touching the harness) and the Meta-Harness
experimenter (overnight config-mutation loop that optimises drydock's
own knobs against the suite). Both feed the same goal — drydock that
holds up under unattended multi-hour workloads — but they exercise
different axes. Hourly Telegram status cron
(`scripts/stress_telegram_status.py`) is still wired; bring it online
with a fresh run when a 2.7.x release should be pressure-tested.

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

### Admiral — in-process supervisor
Admiral is a Textual asyncio task attached to the running `AgentLoop`.
It polls message history every 5 s, runs a battery of detectors over
the recent window, and when a finding qualifies it injects a
`<system-reminder>` via the existing `_inject_system_note` channel.
Never stops the session — interventions are advisory, matching the
project rule in `feedback_no_tool_errors_for_loop_detection.md`.

Shipped (v2.6.137 → v2.7.4):

- **Phase 1 — heuristic detectors.** `detect_tool_call_loop` (3+
  identical calls in a row) and `detect_struggle` (20+ non-write calls
  since the last write). Append-only audit log at
  `~/.drydock/logs/admiral_history.log`; 60 s dedup window so the same
  directive doesn't fire repeatedly.
- **Phase 2 — local-LLM meta-analysis.** When a heuristic finding has
  no canned directive, `llm_analyzer.analyze` asks the local model
  (via the session's own backend, temp=0.1) for a DIRECTIVE or a
  STUMPED response given the last 12 turns plus the detector code.
  If STUMPED, `opus_escalator` escalates to Claude Opus (Anthropic SDK
  if key present, `claude -p` subprocess as fallback) — **only when
  `ADMIRAL_ALLOW_CLOUD_ESCALATION=1`**; default posture is local-only
  to preserve the air-gap pitch.
- **Phase 3a — hyperparameter adaptation.** `task_classifier` labels
  each session (build / bugfix / explore / refactor); `metrics.py`
  writes one JSONL line per finished session; `tuning.py` maintains
  per-(model, task) knob overrides at `~/.drydock/admiral_tuning.json`.
  `apply_to_agent_loop` writes private `_admiral_*` attributes the
  main loop reads — wall-clock budget, hard-stop, warn thresholds,
  temperature, detector windows. Safe no-op until a tuning file exists.
- **Phase 3b — gated code proposals.** Admiral can propose a code-level
  change (not just a directive); proposals are written to
  `~/.drydock/admiral_proposals/` and surface in `admiral_probe`. No
  auto-merge — user reviews and applies.
- **Phase 4+ detectors (v2.7.4 / 2026-04-24).** `detect_empty_after_tool`
  fires when an assistant message after a tool result is empty or pure
  filler; `detect_retry_after_error` fires when the model retries the
  exact failing call after the previous attempt errored. Both wired
  into `run_all`; retroactive validation on 50 sessions from
  `~/.vibe/logs/session/` shows empty 68% / retry 2%, matching the
  mine baseline (61% / 5.8%).
- **Directives rubric** (`Admiral.md`) — the contract Admiral holds
  drydock to. Used in Phase 2 analyzer prompts so directive quality
  stays stable across runs.
- **Observability — `admiral_probe.py`.** Read-only HTTP probe on
  `0.0.0.0:8878` that exposes telemetry as JSON (`history_tail`, PID
  list, drydock version). Pure log-tailer — no drydock imports, so
  restarts are unnecessary on detector code changes.

### Meta-Harness — overnight auto-tuning
Karpathy-style autoresearch scaffold at `research/`. The experimenter
runs a shakedown batch, scores it, and evolves drydock's configuration
between rounds. Proposals come from either a blind random mutator or
an LLM-backed `proposer.py` that reads source + recent traces +
`results.tsv` and suggests ONE contrastive mutation per round.

- **Mutation surface.** `research/config_base.toml` exposes ~16 knobs
  spanning Admiral detector thresholds, harness timings, session-reset
  cadence, and Gemma-4-specific prompts. Target rotation is mandatory
  when one knob class dominates ≥70% of the recent 15 rounds.
- **Airgap-locked proposer.** Default transport is local vLLM via
  `DRYDOCK_RESEARCH_PROPOSER_URL` (the same balancer the TUI uses) and
  `DRYDOCK_RESEARCH_PROPOSER_MODEL=gemma4`. Opus is gated behind
  `--proposer opus` AND an explicit `ADMIRAL_ALLOW_CLOUD_ESCALATION=1`,
  so a self-tuning loop can't silently phone home in an air-gapped
  deployment. Rule recorded in memory as
  `feedback_local_proposer_only.md`.
- **Experimenter safeguards.** Duplicate `(target, name, value)`
  proposals are rejected up front; `mutate_random` reads ban-lists from
  `config_base` (retired knobs don't resurface); no-op integer samples
  are rejected; each promising round gets median-of-3 replication to
  separate signal from variance.
- **Self-heal.** `research_babysitter.sh` hourly cron restarts a stuck
  experimenter. `DRYDOCK_FORCE_VERSION` env var lets the experimenter
  request minor/major bumps without waiting for the normal auto-release
  cadence.
- **Kernel overlay.** Per-round configs are applied as a TOML
  parse+rewrite on the operator's real `~/.drydock/config.toml` (not an
  append), so the test config always sits on top of the user's actual
  providers/credentials and can be rolled back cleanly.

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

## Recent fixes (April 16–24, 2026)

### v2.7.4 + unreleased (April 24) — GitHub issue triage batch
Four user-reported bugs from live TUI usage, each with regression
tests that fail without the fix:

- **Issue #13 — JSONDecodeError on `/v1/chat/completions`,
  `/compact` won't clear it.** A bash tool result containing a raw
  `\x00` or `\x1b` ridden into the conversation history survived
  compaction. `ensure_ascii=True` escaped control chars in the OUTER
  body, but vLLM's tool-call parser re-parses
  `tool_calls.function.arguments` as JSON and choked on the literal
  control byte in the unescaped string. Fix: recursive
  `_strip_control_chars` over the whole payload in `OpenAIAdapter`.
  Mirrors `ReasoningAdapter._sanitize_content` but covers `content`,
  `arguments`, and tool descriptions in one pass.
- **Issue #12 — missing whitespace/newlines after hidden
  reasoning/tool-call blocks.** `_break_walls_of_text` only fires
  when called per-chunk with >200 chars, so streaming chunks never
  trip it. The final accumulated content could still be a long
  no-newline blob. Fix: override `AssistantMessage.stop_stream` to
  run wall-rescue once on the full content at stream end and replay
  the rescued text into the markdown widget.
- **Issue #11 — `task(task=..., agent=...)` printed as plain text.**
  Gemma 4 sometimes emits a real tool call as Python-syntax text
  instead of using the `tool_calls` protocol; nothing ran and the
  TUI sat idle. The v2.6.91 fake-tool-call nuker handled
  `call:name{...}` but not `name(arg=...)`. Added a regex branch that
  matches when the entire content (after peeling optional `thought`
  prefix) is a function-call shape with no real tool_calls, so the
  agent loop's recovery nudge fires.
- **Issue #10 — `todo` loop on `navygpt` (Gemma-derived model).** The
  auto-disable set for loop-prone tools gated on `"gemma" in name`;
  navygpt slipped through under its own alias. Broadened the
  substring hint list to cover known Gemma-derived names and checked
  both `name` and `alias`. Until ModelConfig grows a
  `model_family` field, the list stays explicit.

### v2.7.4 (April 24) — Admiral Phase-4 detectors wired
`detect_empty_after_tool` and `detect_retry_after_error` (shipped as
proposals earlier) are now invoked by `admiral.detectors.run_all` so
they actually fire in live sessions. 9 new unit tests +
retroactive validation on 50 real sessions (empty 68%, retry 2%,
matches 400-session mine baseline).

### v2.7.x research improvements (April 19–22)
- **Meta-Harness integration** — Karpathy-style overnight auto-tuning
  loop shipped in v2.6.146. Experimenter mutates config, runs a
  shakedown batch, logs trace, scores, iterates.
- **Airgap-locked proposer** — local vLLM by default, Opus gated
  behind an explicit env flag. Three layers of protection so a
  self-tuning loop can't silently phone home. See
  `feedback_local_proposer_only.md`.
- **Prompt mutation surface** wired through proposer → experimenter
  → kernel → drydock, so the experimenter can evolve prompt text in
  addition to numeric knobs.
- **Proposer deduplication** — duplicate `(target, name, value)`
  proposals rejected; `mutate_random` respects ban-lists in
  `config_base` (retired knobs don't resurface); no-op integer
  samples rejected; mandatory target rotation when one knob class
  dominates ≥70% of recent 15 rounds.
- **Median-of-3 replication** — each promising round replicated
  twice more to separate signal from variance before it's promoted
  to `config_best`.
- **research_babysitter.sh** hourly cron restarts a stuck
  experimenter; `DRYDOCK_FORCE_VERSION` env var lets the experimenter
  request out-of-band version bumps.

### v2.6.145+ (April 20) — admiral watcher sees raw-markdown leakage
`scripts/stress_watcher.py` tails the stress log for
`[rec-check] ... raw_md=N`, fires a `stress-alert` when ≥3 of the
last ≥10 rec-checks leak raw markdown. Part of the "others on
different machines" observability lift.

### v2.6.144 (April 19) — auto-"Continue." wedge fix + stress hardening
- `DRYDOCK_AUTO_CONTINUE_DISABLE` env gate — the agent-loop
  auto-"Continue." injection was wedging stress runs on text-only
  prompts. Env gate lets the stress harness disable it without a code
  change. See `project_drydock_auto_continue_loop.md`.
- `break user turn on text-only assistant response` — when the model
  emits a text-only turn with no tool calls, the user-turn ends cleanly
  instead of being re-prompted into another tool-call attempt.
- **stress_watcher** catches orphan/retry/skip patterns and actuates
  via SIGUSR1 — the harness respawns the TUI on signal so a wedged
  session doesn't require a babysitter.

### v2.6.141–v2.6.143 (April 17–18) — Admiral Phase 1 through 3b
Three-week arc (in a week): in-process supervisor → local-LLM
meta-analysis → hyperparameter adaptation + gated code proposals.
Details in the Admiral section above. v2.6.142/143 fixed
stress-harness memory leaks that were masquerading as drydock leaks;
v2.6.141 shipped the observation-mode audit after the user's
"let's slow down" directive.

### v2.6.137–v2.6.140 (April 17) — Admiral.md + observability
- `Admiral.md` Directives rubric — the contract Admiral holds drydock
  to, referenced by Phase 2 analyzer prompts.
- `admiral_probe.py` read-only HTTP probe on `0.0.0.0:8878` for the
  dashboard tab (192.168.50.21 box pulls `/api/admiral`).
- `.vibe` → `drydock` refactor across the repo. Log paths moved to
  `~/.drydock/logs/`; `~/.vibe/logs/` still tailed for
  back-compatibility.

### v2.6.134–v2.6.136 (April 17) — user-reported rendering/stop fixes
- **Issue #8** — walls of text in TUI assistant responses. First pass:
  `_break_walls_of_text` promotes inline bold headings and numbered
  lists to real blocks. Reopen fix landed in v2.6.137 with a more
  aggressive pass for the long-prose-no-newlines case.
- **Issue #9** — per-prompt limits too aggressive on hard tasks.
  Budget raised.
- **"when to stop vs continue"** — Gemma 4 was treating mid-turn
  planning as a stop signal. Prompt + nudge adjustments so the model
  only declares done when a real tool call chain has completed.

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

### Currently in flight (April 24, 2026)
- **GitHub issue triage** — 4 user-reported bugs (#10–#13) fixed this
  week, all with regression tests. Remaining open issues should be
  picked up on sight; `feedback_proactive_github_issues.md` codifies
  the rule.
- **Admiral observation mode** — after the "let's slow down" directive
  (v2.6.141), Admiral is wired but the policy cadence is deliberate.
  New detectors (`detect_empty_after_tool`, `detect_retry_after_error`)
  landed in v2.7.4 but won't fire in operator-running TUIs until the
  `.pause_auto_release` flag is lifted or they `pip install -e` the
  source tree.
- **Meta-Harness experimenter** — overnight auto-tuning loop runs
  against the shakedown suite. Active work: proposer rigor
  (dedup, rotation, no-op rejection, median-of-3 replication),
  airgap lock verification. The `research/STOP` sentinel holds the
  loop during manual debugging.
- **Telegram notifications**: `scripts/stress_telegram_status.py`
  still wired; currently idle. Reattach when a long stress run
  should be pressure-tested against a 2.7.x release.

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
