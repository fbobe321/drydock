# Drydock Development Guide

## Project

Drydock is a local CLI coding agent (fork of mistral-vibe, Apache 2.0).
- **Repo:** https://github.com/fbobe321/drydock
- **PyPI:** https://pypi.org/project/drydock-cli/ (v2.6.32)
- **Goal:** Reliable TUI coding agent with local LLMs. PRD-driven project building.
- **Hardware:** 2x RTX 4060 Ti 16GB, Gemma 4 26B MoE (A4B) via vLLM Docker at localhost:8000
- **Server:** remus (Ubuntu 22.04, user: bobef)
- **Active model:** Gemma 4 26B-A4B-it-AWQ-4bit (only 4B active params, ~70 tok/s)
- **The honest test:** `scripts/shakedown_interactive.py` — drives the real TUI
  via pexpect with a multi-step user conversation (24 steps per PRD: plan,
  build, test, add features, debug, review code, edge cases, README).
  Single-prompt shakedown.py also available for quick regression.
- **5 medium-hard test PRDs** at /data3/drydock_test_projects/401-405
  (doc_qa, prompt_optimizer, tool_agent, stock_screener, eval_harness)
  Upgraded 2026-04-11 from easy/medium to medium-hard difficulty.
- **OLD harnesses you should NOT trust:** `scripts/tui_test.py` and
  `core_tests_real.sh` count tool calls and `--help` and miss the things users
  actually experience. Harness pass rates LIE — the only real test is using
  the TUI interactively with multiple rounds of feature requests, edits, and
  testing.
- **Priority:** TUI experience first. Fix drydock bugs, don't simplify PRDs.
- **370 PRDs** at /data3/drydock_test_projects/ — the benchmark suite
- **Current version:** v2.6.32 on PyPI

## Build & Test

### DryDock v3 (active)

```bash
# Install (minimal deps: only openai>=1.0)
cd /data3/drydock-v3 && pip install -e .

# Run drydock CLI (defaults to gemma4 model)
python -m drydock
python -m drydock --model gemma4

# Run programmatically (headless, for benchmarks)
PYTHONPATH=/data3/drydock-v3 python3 -c "import sys; sys.path.insert(0, '/data3/drydock-v3'); from drydock.cli import main; main()"

# Start Gemma 4 model server
bash /data3/Models/start_gemma4.sh

# Check model is running
curl http://localhost:8000/v1/models

# Syntax check modified files
python3 -c "import ast; ast.parse(open('path/to/file.py').read())"
```

### DryDock v2 (TUI — user-facing, v2.5.0)

```bash
# Install
pip install drydock-cli==2.5.0

# Run TUI (the ONLY way to use drydock — no headless mode)
drydock

# Headless mode has been REMOVED. All testing goes through the TUI.
# Use scripts/tui_test.py (pexpect) for automated TUI testing.

# Run tests
uv run pytest tests/ -x -q --timeout=30

# Syntax check modified files
python3 -c "import ast; ast.parse(open('path/to/file.py').read())"
```

## Key Architecture

### DryDock v3 (active — clean rewrite at `/data3/drydock-v3/`)

v3 is a from-scratch rewrite using nano-claude-code as foundation. 4 core files, ~750 lines total. Provider-agnostic, works with any OpenAI-compatible endpoint.

- `drydock/agent.py` — Core agent loop (161 lines). Multi-turn tool calling, event-driven. **Most changes go here.**
- `drydock/providers.py` — LLM abstraction (209 lines). Provider registry (vLLM, Ollama, LM Studio, OpenAI, Anthropic). Streams responses, filters Gemma 4 thinking tokens.
- `drydock/tool_registry.py` — Tool plugin system (46 lines). Register/execute tools by name.
- `drydock/tools/__init__.py` — 6 built-in tools (258 lines): Read, Write, Edit, Bash, Glob, Grep
- `drydock/cli.py` — CLI entry point (213 lines). Default model: gemma4.
- `drydock/compaction.py` — Two-tier context management (119 lines). Normal compaction + emergency compaction on 400 errors.

### DryDock v2 (legacy at `/data3/drydock/`)

- `drydock/core/agent_loop.py` — Main agent loop. Loop detection, message ordering, tool execution.
- `drydock/core/programmatic.py` — Headless API entry point used by SWE-bench harness
- `drydock/core/build_orchestrator.py` — Multi-phase build pipeline (currently disabled, model drives builds)
- `drydock/core/tools/builtins/bash.py` — Shell tool with allowlist/denylist, conda support
- `drydock/core/tools/builtins/search_replace.py` — File editing tool with fuzzy matching
- `drydock/core/prompts/cli.md` — System prompt (workflow, delegation, tool rules)
- `drydock/core/prompts/builder.md` — Minimal prompt for file builder subagents
- `drydock/core/system_prompt.py` — Builds system prompt, loads AGENTS.md/DRYDOCK.md
- `drydock/core/types.py` — `MessageList` (custom Sequence, use `.reset()` not `=[]`)
- `drydock/core/middleware.py` — Tiered context warnings, auto-compaction
- `drydock/core/agents/models.py` — Agent profiles (explore, diagnostic, planner, builder)
- `drydock/core/skills/manager.py` — Skill discovery and loading
- `drydock/cli/textual_ui/app.py` — TUI application
- `drydock/cli/commands.py` — Slash commands (/help, /setup-model, /consult, etc.)

## Critical Constraints

### v3 Constraints
- **Gemma 4 leaks thinking tokens.** The model emits `<|channel>thought<channel|>` tokens that must be filtered out. `providers.py` strips these via regex before displaying output.
- **Two-tier compaction.** Normal compaction truncates old tool results. Emergency compaction triggers on 400 errors (context overflow) with aggressive truncation.
- **Provider-agnostic design.** No model-specific hacks in the agent loop. All model quirks handled in `providers.py`.

### v2 Constraints (TUI codebase)
- **MessageList is not a plain list.** Never `self.messages = [...]`. Use `self.messages.reset([...])`.
- **vLLM/Mistral rejects `user` after `tool` messages.** `_sanitize_message_ordering()` runs before every LLM call. All injection must use `_inject_system_note()`.
- **Non-streaming for Gemma 4.** Streaming tool call argument accumulation produces empty/malformed JSON. `enable_streaming=False` when model is Gemma 4.
- **Disabled tools for Gemma 4:** ask_user_question, todo, task_create, task_update, task, invoke_skill, tool_search. These cause loops or validation errors.
- **Simplified prompt for Gemma 4.** Auto-detected via model name → uses `gemma4.md` (20 lines) instead of `cli.md` (125 lines). Complex prompts cause delegation/asking instead of coding.
- **write_file overwrite=True by default.** Gemma 4 writes files multiple times; old default (False) caused error loops.
- **Headless mode removed.** All interaction through TUI. Testing via pexpect (`scripts/tui_test.py`).
- **NEVER test with headless.** The TUI has different code paths (streaming, approval, path expansion). Headless passes mask TUI bugs.

### Shared Constraints
- **"Restate the goal" blocks tool calling.** Never ask the model to output text before its first tool call — it pre-empts the tool-calling mechanism.
- **Additive-only harness changes.** Every control flow modification (circuit breaker, orchestrator, nudges) caused regressions. Just inject better context before the agent loop.
- **Circuit breaker is disabled.** It blocked valid retries. Loop detection prunes duplicates and nudges instead. Never stops the session.
- **Loop detection never stops.** Only prunes repeated tool calls and injects guidance. The only hard stop is MAX_TOOL_TURNS (200).

## Gemma 4 Tool Calling (v3 — active)

- **Model:** Gemma-4-26B-A4B-it-AWQ-4bit (26B MoE, only 4B active params per token)
- **Serving:** vLLM Docker image `vllm/vllm-openai:gemma4` with `--tool-call-parser gemma4 --enable-auto-tool-choice`
- **Start script:** `/data3/Models/start_gemma4.sh` (Docker: 2 GPU tensor parallel, 131K context, fp8 KV cache)
- **Performance:** 3-4x faster than devstral-24B, 0% timeouts on SWE-bench, 70% file match (vs 60% devstral)
- **Thinking tokens:** Model leaks `<|channel>thought<channel|>` — filtered in `providers.py` via regex
- **Tool choice:** `tool_choice="auto"` (default), model natively decides tool vs text
- **No AGENTS.md dependency:** Gemma 4 does not loop without AGENTS.md (unlike devstral)
- **Docker management:** `docker stop gemma4 && docker rm gemma4` to restart

## Mistral/devstral Tool Calling (v2 — legacy)

- `tool_choice="auto"` (default): model decides whether to call a tool or respond with text
- Never ask for text before first tool call — blocks tool calling
- `tool_choice="required"` forces a tool call (useful for first-turn delegation)
- AGENTS.md anchors model behavior — without it, devstral loops
- Plan→Edit workflow: use plan mode first, then switch to accept-edits

## File Locations

```
/data3/drydock-v3/                 ← DryDock v3 (active, clean rewrite)
├── drydock/                       ← Python package (~750 lines total)
│   ├── agent.py                   ← Core agent loop (THE most important file)
│   ├── providers.py               ← LLM abstraction (vLLM, Ollama, etc.)
│   ├── tool_registry.py           ← Tool plugin system
│   ├── tools/__init__.py          ← 6 built-in tools (Read, Write, Edit, Bash, Glob, Grep)
│   ├── cli.py                     ← CLI entry point
│   ├── compaction.py              ← Two-tier context management
│   ├── memory/                    ← Memory system
│   └── skills/                    ← Skill framework
├── scripts/
│   └── monitor.sh                 ← Process monitor (restarts Gemma 4 if needed)
├── logs/                          ← Runtime logs
├── pyproject.toml                 ← v3.0.0, depends only on openai>=1.0
└── AGENTS.md                      ← Project instructions
```

```
/data3/drydock/                    ← DryDock v2 (legacy, original codebase)
├── drydock/                       ← Python package
│   ├── core/
│   │   ├── agent_loop.py          ← v2 agent loop
│   │   ├── prompts/cli.md         ← System prompt
│   │   ├── build_orchestrator.py  ← Multi-phase builder (disabled)
│   │   ├── tools/builtins/        ← 24 builtin tools
│   │   ├── agents/                ← Subagent definitions
│   │   └── skills/                ← Skill framework
│   ├── cli/textual_ui/app.py     ← TUI application
│   └── skills/                    ← 7 bundled skills
├── tests/
│   ├── test_smoke.py              ← 20 fast tests
│   ├── test_loop_detection.py     ← 38 loop tests
│   ├── test_bank_prd.py           ← 15 PRD-driven tests
│   └── testbank_helpers.py        ← Shared test infrastructure
├── scripts/
│   ├── deploy_to_github.sh        ← Daily push (4 AM)
│   ├── publish_to_pypi.sh         ← Tests → build → PyPI → tag
│   ├── notify_release.py          ← Telegram notifications
│   └── backup.sh                  ← rsync to NAS (3 AM)
└── test_bank_results/             ← Test logs
```

```
/data3/swe_bench_runs/             ← SWE-bench infrastructure (separate)
├── harness.py                     ← Runs drydock on SWE-bench tasks (supports v2 + v3 backends)
├── continuous_bench.sh            ← Continuous improvement loop (v2/devstral)
├── continuous_gemma4.sh           ← Continuous improvement loop (v3/Gemma 4, 20-task batches)
└── logs/                          ← Batch results
```

```
/data3/Models/                     ← Model files and startup scripts
├── Gemma-4-26B-A4B-it-AWQ-4bit/  ← Gemma 4 model weights
└── start_gemma4.sh                ← Docker startup (vLLM + Gemma 4)
```

## Configuration Management

**All scripts use explicit Python path:** `/home/bobef/miniconda3/bin/python3`
**Cron PATH doesn't inherit shell.** Every script exports PATH explicitly.
**User's DryDock install:** `/home/bobef/miniforge3/envs/drydock/bin/python3` (Python 3.14)
**Dev/test Python:** `/home/bobef/miniconda3/bin/python3` (Python 3.12)
**Config:** `~/.drydock/config.toml`
**Tokens:** `~/.config/drydock/github_token`, `~/.config/drydock/pypi_token`
**Git remotes:** `drydock` → github.com/fbobe321/drydock, `origin` → github.com/mistralai/mistral-vibe

## Deployment Process

1. Code → modify files
2. Syntax check → `python3 -c "import ast; ..."`
3. Tests → `pytest tests/test_smoke.py tests/test_loop_detection.py -q`
4. Commit → descriptive message
5. Publish → `./scripts/publish_to_pypi.sh` (tests → build → PyPI → tag → integration test → GitHub → Telegram)
6. Install on user's env → `/home/bobef/miniforge3/envs/drydock/bin/pip install --force-reinstall --no-deps --no-cache-dir drydock-cli==X.Y.Z`

## SWE-bench

### Current Results (Gemma 4 + DryDock v3)
- **70% file match** (vs 60% devstral v2), 0% timeouts, 8x faster per task
- Harness at `/data3/swe_bench_runs/harness.py` supports both v2 (devstral) and v3 (gemma4) backends
- Continuous bench loop: `/data3/swe_bench_runs/continuous_gemma4.sh` (20-task batches with Telegram updates)
- AGENTS.md auto-created per task with bug-fix workflow
- Environment bootstrapping (Meta-Harness technique) gathers repo structure before first LLM call

### Previous Results (devstral + DryDock v2)
- 42% file match, 62% real patches (50-task eval)
- Target was 68% (devstral-small-2 published score)
- Continuous bench loop: `/data3/swe_bench_runs/continuous_bench.sh`

## Improvement Backlog (from research papers + industry analysis)

**Meta-Harness (arXiv:2603.28052) — still TODO:**
- Execution trace logging (full prompts + tool calls per task for failure diagnosis)
- Repo-specific routing (different strategies per SWE-bench repo)
- Draft-then-verify (generate patch, verify against tests before committing)
- Contrastive examples (similar bugs with different fixes)
- Pareto frontier tracking (accuracy vs token cost)

**Industry design patterns — still TODO:**
- Parallel tool execution (multiple independent tool calls per turn)
- Background memory consolidation (clean context during idle time)
- Three-tier memory system (index + topics + archives)
- Frustration detection (regex-based, adjust approach when user is stuck)
- Extended reasoning allocation (detect complex tasks, give more compute)
- Git safety guards (stash before destructive ops)

**Implemented:**
- ✅ Static/dynamic prompt split (prefix caching)
- ✅ AGENTS.md auto-creation
- ✅ Environment bootstrapping
- ✅ Additive-only harness changes
- ✅ Permission denial feedback (suggest alternatives)
- ✅ "Task Completed" false claim detection
- ✅ Loop guidance (never stops, only redirects)
- ✅ Composable prompt (static first, dynamic after)
- ✅ DryDock v3 clean rewrite (provider-agnostic, ~750 lines)
- ✅ Gemma 4 MoE model (3-4x faster, 70% file match)
- ✅ Two-tier context compaction (normal + emergency on 400 errors)
- ✅ Docker-based model serving (vLLM + Gemma 4)
- ✅ Multi-backend SWE-bench harness (v2/devstral + v3/gemma4)
- ✅ Thinking token filtering (Gemma 4 `<|channel>` leak)
- ✅ **Shakedown harness** (`scripts/shakedown.py`) — drives real TUI
  via pexpect, watches live session log, types `STOP` interrupts,
  judges on user-perceptible criteria
- ✅ **Shakedown suite** (`scripts/shakedown_suite.sh`) — 10 core
  projects through the shakedown harness
- ✅ **`_check_main_module_entry`** in write_file.py — catches
  `__main__.py` files that import `main` but never call it (the codec
  silent-exit bug)
- ✅ **`_check_missing_sibling_imports`** in write_file.py — catches
  `from .cli import CLI` when `cli.py` doesn't exist on disk yet (the
  minivc unimportable-package bug)
- ✅ **Escalating dedup message** in write_file.py — 2nd+ identical-content
  write to a path returns the actual current directory listing plus
  concrete next-action suggestions, instead of abstract "move to next file"
- ✅ **Loop-detection wiring** in agent_loop.py — `_check_tool_call_repetition()`
  was defined but never called; now fires from `_handle_tool_response()`
  and injects an advisory nudge via `_inject_system_note()`, rate-limited
  every 3 turns
- ✅ **`_truncate_old_tool_results`** in agent_loop.py — proactive
  shrinkage of stale tool results before each LLM call; keeps the last
  6 in full, truncates older ones > 800 bytes to head + footer + size
  marker; idempotent
- ✅ **`_task_manager.py` rename** — TaskCreate/Update/List were
  duplicates of the `todo` tool that confused Gemma 4 into hanging.
  Underscore prefix excludes them from tool discovery
- ✅ **Trust dialog auto-dismissal** in shakedown.py
- ✅ **Pause flags** for both `auto_release.sh` and `watchdog.sh` so
  manual debugging doesn't get its work overwritten
- ✅ **Adaptive thinking** — thinking=OFF for routine file writes,
  thinking=HIGH for planning (first 4 msgs) and user messages,
  thinking=LOW for error recovery. Eliminates 30-120s hangs between
  file writes. Mirrors Claude Code's extended thinking approach.
- ✅ **search_replace "already applied" detection** — when search text
  is gone but replacement text already exists, return success+warning
  instead of "not found" error. Stops the #1 edit loop pattern.
- ✅ **search_replace file_path inference** — when model drops file_path
  (Gemma 4 does this frequently), scan project files for the search text
  and auto-fill the path.
- ✅ **search_replace raw-code fallback** — when model sends raw code
  without SEARCH/REPLACE markers, fall back to full file overwrite
  instead of error-looping.
- ✅ **Thinking token stripping** — strip ALL `<|channel>` variants
  (thought, call, tool_call, double-channel) from message history
  before storing. Saves context, prevents confusion.
- ✅ **Thinking-stall nudge** — detect empty responses (pure thinking,
  no content/tools) after tool results. Pop empty message, inject
  "Continue working" nudge. Max 2 retries.
- ✅ **Todo tool arg coercion** — Gemma 4 sends todos as strings
  ("1. Do X\n2. Do Y") or flat lists (["X","Y"]). Validator coerces
  to proper TodoItem dicts.
- ✅ **Hallucinated tool suppression** — silently ignore
  exit_plan_mode, enter_plan_mode (Gemma 4 invents these).
- ✅ **Subagent progress streaming** — stream ToolCallEvents from
  builder subagent to TUI so user sees "→ Writing file.py" instead
  of "Sailing... 10m" silence.
- ✅ **Interactive shakedown** (`scripts/shakedown_interactive.py`) —
  24-step user scripts per PRD: plan, todo, build, test, code review,
  add features via search_replace, bug hunt, ideas, README.
- ✅ **Delegation threshold raised** 6→9 files. Prompt rule: if user
  asks to PLAN or EXPLAIN, respond with text — don't delegate.
- ✅ **Auto-continue instruction** — gemma4.md: "execute ALL todo
  items without pausing, only stop when EVERY item is done."
- ✅ **Harder PRDs (v2)** — all 5 test PRDs upgraded from easy/medium
  to medium-hard (2026-04-11):
  - doc_qa: +BM25 algorithm, incremental updates via SHA-256, corpus
    stats, delete command (6→8 files)
  - tool_agent: +multi-step chaining, pipe syntax, plugin discovery,
    conversation memory (5→8 files)
  - stock_screener: +sector grouping, watchlist save/load, snapshot
    comparison, percentile ranking (6→9 files)
  - eval_harness: +evaluator pipelines, bootstrap significance testing,
    diff reports, per-category breakdown (6→8 files)
  - prompt_optimizer: +tournament selection, crossover mutation, F1
    scoring, bootstrap CI, train/test split, lineage tracking (7→8 files)
- ✅ **Doc_qa name confusion fix** — model was creating `doc_qa_rag/`
  instead of `doc_qa/` because old dir was `401_doc_qa_rag`. Fixed:
  AGENTS.md now explicitly states package name, harness cleanup removes
  stale dirs matching the package prefix, and dot-storage dirs from
  previous runs.
- ✅ **Stale dir cleanup in harness** — `run_interactive()` now removes
  any directory in cwd that starts with the package name prefix but
  isn't the expected package dir (catches model naming mistakes).

**Legal note:** All patterns are standard design concepts implemented from scratch. No proprietary code copied.

## Key Learnings

### Most-recent (debugging the user's worst session, April 2026)

1. **Test-harness counts are not user pain.** `tui_test.py` and
   `core_tests_real.sh` reported 80% pass while real users saw drydock
   loop, hang, and produce broken code. The harnesses were structurally
   incapable of catching what users experience because they measure
   tool-call counts and `--help` exit codes, not progress. Build pass
   criteria around user-perceptible state instead. See `shakedown.py`.
2. **Gemma 4 ignores advisory nudges.** The model received the dedup
   message ("File already has this exact content. Move to the NEXT file."),
   the loop-detection system note, the missing-import warning, AND a
   user-typed `STOP` interrupt — its own thinking tokens ACKNOWLEDGED
   the loop ("The user is pointing out that I am in a loop... I should
   move to the next file") — and kept writing the same file 10 times
   anyway. The advisory-only rule is correct in principle, but Gemma 4
   cannot reliably respond to it. Hard blocks on pure no-op duplicates
   may eventually be necessary.
3. **The auto_release.sh cron silently reverts site-packages every 6 hours.**
   It runs at 0/6/12/18, builds a wheel from `/data3/drydock`, uploads to
   PyPI, and `pip install --force-reinstall`s into the user's env. Direct
   edits to `site-packages/drydock/...` disappear at the next cron tick.
   Pause via `touch /data3/drydock/.pause_auto_release`.
4. **`/data3/drydock` is in `sys.path`, but site-packages comes first.**
   `import drydock; print(drydock.__file__)` lies depending on cwd. From
   `/data3/drydock` it reports the source tree (cwd is `""` at index 0).
   From any project cwd it reports site-packages. **Always validate
   imports from a neutral cwd.**
5. **PRDs get contaminated across sessions.** The model edits `PRD.md`
   during a session (adds "✅ Completed" status tables, chat-style
   filler text). The next test run sees a "completed" PRD and either
   declares done immediately or hallucinates fix actions. Snapshot
   `PRD.master.md` before each run; the harness restores it automatically.
6. **Hallucinated tool names hang the session.** The model called
   `ralph_repo_index({"directory": "."})` — a tool that doesn't exist.
   `_build_tool_call_events()` had `if tool_class is None: continue` which
   silently dropped the call. Drydock waited forever for a tool result
   that never came. `resolve_tool_calls()` correctly emits a `FailedToolCall`
   downstream — both code paths exist; verify which one the model is hitting.
7. **The "Trust this folder?" dialog blocks all input.** Drydock pops a
   blocking modal the first time it opens an unfamiliar directory. The
   harness used to type the user prompt blindly into it; the dialog ate
   the keystrokes and drydock waited forever. The fix: detect the dialog
   in pexpect output, send Left+Enter to answer Yes. The harness now does
   this. The CLI should never strand users on a modal dialog.
8. **Context-bloat eats first-turn latency.** Drydock includes the system
   prompt + 24 tool definitions + AGENTS.md auto-injection + user message
   on every turn. With Gemma 4 `thinking="high"` the first response can
   take 60–130 seconds even on a small project, because the model is
   generating ~1000 thinking tokens at ~70 tok/s. `auto_compact_threshold`
   defaults to 200K (higher than Gemma 4's 131K max context) so it never
   fires. Lower it per-model, and rely on `_truncate_old_tool_results()`
   to shrink stale `read_file` outputs proactively.
9. **`task_manager.py` (TaskCreate/Update/List) confused Gemma 4** — the
   model mixed it up with the existing `todo` tool, called `task_update`
   on a `todo` ID, hung waiting on the response. Renamed to
   `_task_manager.py` so the auto-discovery loop in `tools/manager.py`
   skips it (it skips files starting with `_`). The classes are still
   importable for tests via the new module name.
10. **Variance is the rule, not the exception.** Same code, same prompt,
    same model: codec passed in run 1 of the suite and triggered all 3
    pain criteria in run 2. Always state both numbers honestly. Don't
    cherry-pick the run that worked.

### Older but still relevant

11. **Test the TUI, not headless.** Headless passes mask real bugs. TUI
    has different code paths (streaming, approval, render_path_prompt).
    ALL testing through TUI via pexpect.
12. **Use PRDs for testing.** Concrete PRDs with expected files give
    clear pass/fail. The 370-PRD suite is at /data3/drydock_test_projects/.
13. **Scaffold per model.** Weaker models need more guardrails
    (non-streaming, fewer tools, simpler prompts). As models improve,
    remove scaffolding.
14. **Streaming breaks Gemma 4 tool calls.** Non-streaming gets the
    complete response so JSON args are valid. Set `enable_streaming=False`
    when model is Gemma 4.
15. **Fewer tools = better for local models.** 24 builtins is already a
    lot for Gemma 4. The auto-injected list of 39 user-installed CLI
    tools makes it worse.
16. **Simple prompts win.** The 125-line `cli.md` with phases/delegation
    confused Gemma 4. The 20-line `gemma4.md` ("ACT IMMEDIATELY") works.
17. **AGENTS.md essential for devstral, not needed for Gemma 4.**
18. **Circuit breaker was net negative — removed entirely.**
19. **Loop detection should guide, never stop.** Per the user's rule.
    See learning #2 for the limit of this rule.
20. **Auto-read on failed edit, temperature bump on loops, failed-approach
    accumulator** — additive context techniques that help.

### April 2026: interactive shakedown learnings

21. **Harness pass rates lie — interactive testing finds real bugs.**
    Single-prompt "build the package" tests showed 93% pass rate.
    Interactive 24-step conversations (plan, build, test, add features,
    debug, review) found: thinking hangs, todo tool broken, search_replace
    format errors, model stopping to ask permission, no subagent visibility,
    exit_plan_mode hallucination. NONE of these showed up in the harness.
22. **Thinking hangs are the #1 user pain.** With thinking=HIGH on every
    turn, the model generates 30-120s of thinking tokens between file writes.
    Users see the TUI frozen. Fix: adaptive thinking — OFF for routine
    writes, HIGH only for planning and user messages.
23. **search_replace fails 3 ways.** (a) Model drops file_path entirely.
    (b) Model sends raw code without SEARCH/REPLACE markers. (c) Model
    retries an edit that already succeeded. Each needs a different fix.
24. **Gemma 4 hallucinates tools.** Commonly: exit_plan_mode,
    enter_plan_mode, list_mcp_resources. Silently drop them instead of
    showing errors.
25. **Subagent work is invisible.** Builder subagent runs 10+ minutes
    writing files and testing. User sees "Sailing..." with no progress.
    Fix: stream ToolCallEvents from subagent to main TUI.
26. **Model stops at each todo item.** Despite "NEVER ask shall I
    continue", Gemma 4 completes one phase then stops and reports.
    Needs stronger instruction: "execute ALL items without pausing."
27. **site-packages overrides source tree.** Even with `-e` install,
    old `.pyc` files or stale pip installs in site-packages can mask
    source changes. Always `pip install -e /data3/drydock` after changes.
28. **The Trust dialog blocks automation.** It appears on first launch
    in any new directory, eats the user's prompt, and the harness can't
    see it. Pre-trust all test directories in trusted_folders.toml.
29. **Directory names contaminate package names.** When the project
    directory was `401_doc_qa_rag`, the model created `doc_qa_rag/`
    instead of `doc_qa/` as specified in the PRD. The directory name
    leaks into the model's reasoning even when the PRD is explicit.
    Fix: (a) rename directories to match package names, (b) make
    AGENTS.md explicitly state the package name and directory, (c)
    harness cleanup must remove stale dirs with wrong names.
30. **Harder PRDs expose different failure modes.** Medium-difficulty
    PRDs (5-6 files, simple algorithms) passed 5/5 on interactive.
    Medium-hard PRDs (8-9 files, BM25/tournament selection/pipelines)
    are a better signal for whether drydock can handle real user work.
    Easy PRDs were ceiling-ed out — all passing means you can't tell
    what's broken.
31. **Harness bugs faked all previous pass rates.** (2026-04-12) The
    shakedown harness typed prompts via PTY but never verified the TUI
    actually accepted them. Result: 25 out of 28 prompts were silently
    dropped per session — the TUI only saw the first 3-5 prompts, and
    the harness counted assistant/tool messages from that ongoing work
    as "condition met." Every "24/24 passed" was a lie. Fix: wait for
    a NEW user-role message to appear in the session log after typing,
    retry up to 3x if it doesn't. Also wait for TUI to go idle (6s no
    new messages) before typing the next prompt.
32. **`pkg_works` via `--help` was the exact bug CLAUDE.md warns
    against.** (2026-04-12) I reintroduced `python3 -m pkg --help` as
    the pass criterion. Every PRD "passed." Actually only 3/11 worked
    — the rest had broken imports, missing features, fake outputs.
    Replace with `functional_tests.sh` per PRD that runs real feature
    commands and checks real outputs (e.g., calculator must compute
    23*47=1081, lang_interp must run let x=42; print(x*2) and output 84).
33. **Have the TUI run the tests, not the harness.** Externally
    running `functional_tests.sh` tests the CODE, not drydock's
    ability to test-and-fix. Real test: TUI runs tests via bash tool,
    observes failures, reads failing files, applies fixes, iterates.
    Harness only observes the session log for 'RESULT: X passed, Y failed'.
34. **RALPH loop works much better than pipelined prompts.** (2026-04-12)
    `shakedown_interactive.py` with 24-30 prompts racing the TUI never
    landed properly. `ralph_loop.py` with ONE prompt → wait for
    completion → run tests → send failures back → iterate works great.
    Got 10/11 packages to 100% test pass via this approach in one night.
35. **Auto-rollback on test regression is essential.** Gemma 4 26B with
    only 4B active params regresses passing tests when trying to fix
    failing ones. Snapshot the package dir before each fix iteration;
    if score goes down, restore. Even with "don't break passing tests"
    in the prompt, the model will destroy working code.
36. **Drydock takes 3-5 MINUTES to create a session dir after spawn.**
    (2026-04-12) When GPU is busy from a prior run, drydock may take
    up to 4 minutes before the session directory is even created. My
    180s wait was timing out with 1 second to spare. Bumped to 300s.
37. **meta.json is only written at session EXIT.** (2026-04-12) Can't
    match sessions by working_directory during an active session.
    `find_session()` must match on directory creation time relative
    to harness start, not meta.json content. messages.jsonl is also
    inconsistently flushed during the session.
38. **NEVER use broad pkill/kill matching 'drydock'.** (2026-04-12) I
    killed the user's active TUI session twice by doing
    `ps aux | grep drydock | kill`. Only kill by tracked PIDs from
    my own background tasks. User's TUI is also a drydock process.

## Testing

### Use `scripts/shakedown_interactive.py` — multi-round conversations

The interactive shakedown is the REAL test. Two modes:

1. **Interactive (default):** 24 prompts per PRD simulating a real user:
   plan → build → test → add features → debug → review → README
2. **Autonomous:** Single mega-prompt (14-16 items), model must
   self-manage the entire checklist without user interaction

```bash
# Interactive test (24-step conversation)
python3 scripts/shakedown_interactive.py \
    --cwd /data3/drydock_test_projects/403_tool_agent \
    --pkg tool_agent

# Autonomous test (single mega-prompt)
SHAKEDOWN_MODE=autonomous python3 scripts/shakedown_interactive.py \
    --cwd /data3/drydock_test_projects/403_tool_agent \
    --pkg tool_agent

# Quick regression (single prompt, separate script)
python3 scripts/shakedown.py \
    --cwd /data3/drydock_test_projects/403_tool_agent \
    --prompt "review the PRD and build the package" \
    --pkg tool_agent
```

### PRD difficulty tiers

The 5 test PRDs (401-405) are at **medium-hard** difficulty:

| PRD | Package | Files | Key challenges |
|-----|---------|-------|----------------|
| 401 | doc_qa | 8 | TF-IDF + BM25, incremental updates, SHA-256 hashing |
| 402 | prompt_optimizer | 8 | Tournament selection, crossover, F1, bootstrap CI |
| 403 | tool_agent | 8 | Multi-step chaining, pipe syntax, plugins, memory |
| 404 | stock_screener | 9 | Sectors, watchlists, snapshot compare, percentile rank |
| 405 | eval_harness | 8 | Evaluator pipelines, bootstrap stats, diff reports |

All are stdlib-only. If the model can pass these in both interactive and
autonomous modes, it can handle real user sessions.

### Old harness note

The `shakedown.py` single-prompt harness catches what
real users experience. Older harnesses (`tui_test.py`, `core_tests_real.sh`,
`run_real_tests.sh`) measure tool counts and `--help` exit codes; they pass
while real users see loops and hangs.

**How it works:**
- Drives the real `drydock` TUI via pexpect (no headless mode)
- Sends a vague user-style prompt ("review the PRD and build the package")
- Polls the live `~/.vibe/logs/session/session_<id>/messages.jsonl` in parallel
- Watches for write loops, search_replace cascades, hallucinated-tool hangs
- Types simulated `STOP` interrupts when loops are detected, then tracks
  whether the model OBEYS them
- Distinguishes three end-states:
  - **Active turn** (any new message in the last 120s) → keep going
  - **Done** (last assistant has text content with no tool call) → grace 30s, then PASS
  - **Dead silence** (no new messages of any kind for 120s) → FAIL
- Resets the cwd between runs (restores `PRD.md` from `PRD.master.md` if present,
  wipes the package dir and stale data dirs)
- Auto-handles the "Trust this folder?" dialog drydock pops on new directories

**Pass criteria (ALL must hold):**
1. NO write loops (≥3 identical-content writes to a path)
2. NO ignored user `STOP` interrupts
3. NO search_replace failure cascade (≥3 in a row)
4. `python3 -m <pkg> --help` actually works
5. Session under `MAX_SESSION_SECONDS` (default 600)

**Run a single project:**

```bash
PYTHONUNBUFFERED=1 python3 -u scripts/shakedown.py \
    --cwd /data3/test_drydock \
    --prompt "review the PRD and get started" \
    --pkg doc_qa_system
```

**Run the 10-project core suite:**

```bash
bash scripts/shakedown_suite.sh
```

**Results history:**

*Medium-difficulty PRDs (before 2026-04-11):*
- Interactive 24-step: 5/5 all steps pass, 4/5 packages work
  (doc_qa failed — model created `doc_qa_rag/` instead of `doc_qa/`)
- Autonomous mega-prompt: 5/5 pass, avg 123s per PRD
- tool_agent: 24/24, 365s, pkg YES
- stock_screener: 24/24, 277s, pkg YES
- eval_harness: 24/24, 435s, pkg YES
- doc_qa: 24/24, 300s, pkg NO (name confusion — fixed)
- prompt_optimizer: 24/24, 310s, pkg YES

*Medium-hard PRDs (2026-04-11):*
- Interactive 24-step: 5/5 packages work, 3/5 get 24/24 steps
  - tool_agent: 24/24, 323s, pkg YES
  - stock_screener: 24/24, 353s, pkg YES
  - eval_harness: 21/24, 792s, pkg YES (3 steps timeout)
  - doc_qa: 22/24, 693s, pkg YES (2 steps timeout)
  - prompt_optimizer: 24/24, 431s, pkg YES
- Autonomous mega-prompt: 4/5 pass, 1 flaky (eval_harness thinking stall)
- Key fix: autonomous prompts now write cli.py FIRST + pkg_works condition
- **NOTE: These were fake pass rates. The harness was dropping 25/28 prompts
  silently. Real results via functional_tests.sh (below) showed only 3/11
  packages actually worked.**

*Functional test results (2026-04-12, post harness fixes, v2.6.38):*
- Ladder: 5-min, 15-min, 30-min, 60-min tiers (complexity, not duration)
- Session started: 29/49 tests passing (59%) via real functional_tests.sh
- Session ended: **49/52 tests passing (94%)** via ralph_loop.py
  - doc_qa: 5/5, prompt_optimizer: 4/4, tool_agent: 5/5
  - stock_screener: 4/4, eval_harness: 4/4, mini_db: 4/7
  - site_gen: 5/5, lang_interp: 5/5, pkg_manager: 4/4
  - web_frame: 4/4, build_sys: 5/5
- Only mini_db not at 100%: WHERE filter, UPDATE, DELETE don't work
- Key tooling: scripts/ralph_loop.py replaces shakedown_interactive.py
  as the primary test harness. One big prompt, observe, fix, iterate.

*Small suite (older):*
- 4/4 (roman_converter, prime_tool, todo_list, codec) with functional
  verification. Variance is real — codec passed in one run and FAILED
  with a 3-criteria loop in the next. Same code, same prompt.

### Fixture: `tests/fixtures/doc_qa_system_prd.md`

The PRD that originally exposed the user's worst session (10 identical
`__init__.py` writes, hallucinated `ralph_repo_index` tool, contaminated
PRD across sessions, 13-min runtime) is checked in. Use it as the canary
case for any harness work.

### Two cron pause flags you should know about

Both crons silently overwrite work mid-debug. Pause them when iterating:

```bash
touch /data3/drydock_test_projects/.pause_watchdog       # stops watchdog.sh
touch /data3/drydock/.pause_auto_release                  # stops auto_release.sh
```

`auto_release.sh` runs at 0/6/12/18, builds a wheel from `/data3/drydock`,
uploads to PyPI, and `pip install --force-reinstall`s it into the user's env.
That overwrites any direct site-packages edits. **If your fix doesn't
survive the next cron run, it's because you only edited `/data3/drydock` —
auto_release rebuilds from there but ALSO replaces site-packages.** Commit
your fix to source AND verify it landed via `git log --oneline -5` before
expecting persistence.

### Why direct site-packages edits keep disappearing

`/home/bobef/miniforge3/envs/drydock/lib/python3.14/site-packages` comes
before `/data3/drydock` in `sys.path` from a project cwd, even though the
`_drydock.pth` file adds `/data3/drydock`. So a sanity check from
`/data3/drydock` (`import drydock; print(drydock.__file__)`) lies — it
reports `/data3/drydock/drydock/__init__.py` because cwd-`""` is at index 0.
From `/data3/drydock_test_projects/X` it reports the site-packages path.
**Always test imports from a neutral cwd**, OR commit to source AND let
auto_release rebuild.

### Test Levels (kept for reference, but the shakedown harness covers all of them)
1. **Build test (--help):** package runs — MEANINGLESS alone
2. **Functional test:** PRD test cases verified with correct output — THE REAL TEST
3. **Acceptance test:** specific expected outputs verified
4. **Quality test:** code quality checks (docstrings, no eval, error handling)

### Critical Testing Rules

🚨 **`--help` IS NOT A TEST.** 🚨 It never was, never will be. `python3 -m
pkg --help` succeeding means nothing about whether the code works. A package
that `--help`s successfully can have:
- Broken imports in every submodule except cli.py
- Methods that raise NotImplementedError when actually called
- Fake outputs that look right but come from hardcoded strings
- Algorithms that return `[]` for every input

**NEVER use `--help` as a pass criterion.** This was re-learned on 2026-04-11
when I accidentally added `pkg_works = python3 -m pkg --help` as a success
signal. Every PRD passed. None of them were actually tested. The signal was
worthless.

**THE REAL TEST** is a `functional_tests.sh` per PRD that runs actual feature
commands against realistic inputs and checks the outputs match expectations.
If there's no functional test, there's no test — the run is UNTESTED, not PASS.

Other rules:
- PRD failures = DryDock bugs. Fix the agent, NOT the PRD.
- Safety mechanisms must be ADVISORY not BLOCKING
- NEVER add circuit breakers that prevent legitimate work
- The model should be able to answer questions without being forced to edit files
- **NEVER trust an old harness's pass result.** If `tui_test.py` or
  `core_tests_real.sh` says PASS, run the same project through
  `shakedown.py` to confirm. The old harnesses count `--help` and
  silently miss the things users hate.
- **Always reset PRDs between runs.** The model edits PRD.md (adds
  "✅ Completed" status tables, chat-style filler) and contaminates
  subsequent test runs. The harness restores from `PRD.master.md`
  automatically if present.

## When Compacting

Always preserve: the list of files modified in this session, any failing test output, current SWE-bench results, and the user's latest instructions.

## Style

- Python 3.12+, type hints, pathlib over os.path
- Match existing patterns in the codebase
- No unnecessary abstractions — keep fixes minimal
- Always run syntax check after editing .py files
- Don't add features beyond what was asked
