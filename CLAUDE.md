# Drydock Development Guide

## Project

Drydock is a local CLI coding agent (fork of mistral-vibe, Apache 2.0).
- **Repo:** https://github.com/fbobe321/drydock
- **PyPI:** https://pypi.org/project/drydock-cli/ (v2.5.0)
- **Goal:** Reliable TUI coding agent with local LLMs. PRD-driven project building + SWE-bench bug fixing.
- **Hardware:** 2x RTX 4060 Ti 16GB, Gemma 4 26B MoE (A4B) via vLLM Docker at localhost:8000
- **Server:** remus (Ubuntu 22.04, user: bobef)
- **Active model:** Gemma 4 26B-A4B-it-AWQ-4bit (only 4B active params, ~70 tok/s)
- **TUI results:** 88/90 PRD projects pass (98%) through the real TUI
- **SWE-bench:** 70% file match (v3 baseline), 58% (v3 tuned — tighter prompt hurt)
- **Priority:** TUI experience first, SWE-bench second

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

**Legal note:** All patterns are standard design concepts implemented from scratch. No proprietary code copied.

## Key Learnings

1. **Test the TUI, not headless.** Headless passes mask real bugs. TUI has different code paths (streaming, approval, render_path_prompt). ALL testing through TUI via pexpect.
2. **Use PRDs for testing.** Concrete PRDs with expected files and `--help` give clear pass/fail. 100-project suite proved TUI works (98%).
3. **Scaffold per model.** Weaker models need more guardrails (non-streaming, fewer tools, simpler prompts). As models improve, remove scaffolding. The code should get simpler over time.
4. **Streaming breaks Gemma 4 tool calls.** Streaming accumulates JSON chunk-by-chunk → empty args. Non-streaming gets complete response → works perfectly. Root cause of weeks of TUI failures.
5. **Fewer tools = better for local models.** Gemma 4 wasted 64 turns on task_create/task_update. Disabling non-essential tools eliminated the loop.
6. **Simple prompts win.** The 125-line cli.md with phases/delegation confused Gemma 4. The 20-line gemma4.md ("ACT IMMEDIATELY") works.
7. AGENTS.md essential for devstral (loops without it), not needed for Gemma 4
8. Circuit breaker was net negative — removed entirely
9. Loop detection should guide, never stop (retrospection > hardcoded nudges)
10. Clean rewrites beat incremental fixes — v3 (~750 lines) outperforms v2 (~5000+ lines) on SWE-bench
11. Model choice matters more than agent complexity — Gemma 4 MoE gives 3-4x speed
12. Docker-based model serving (vLLM) is reliable — easier restarts, GPU management
13. Auto-read on failed edit — automatically show the model the real file content
14. Temperature bump on loops — force model to explore different paths
15. Failed-approach accumulator — prevent re-trying strategies after pruning/compaction
16. PRD complexity must match model capability — too many files/features causes timeout

## Testing

### TUI Test Harness
- **Script:** `scripts/tui_test.py` — pexpect-based, drives the REAL TUI binary
- **Auto-approves** tool permission prompts
- **Detects:** write loops, API errors, tool call counts, file creation, --help pass/fail
- **100 PRDs:** at `/data3/drydock_test_projects/` (88/90 pass = 98%)

### PRD Format (what works)
- Short (under 30 lines)
- 4-6 files max
- Clear package name and file list
- Test command: `python3 -m package_name --help`
- Stdlib only (no external deps)

### What breaks PRDs
- Too many files (8+) — model runs out of turns
- Complex parsers (custom YAML/XML from scratch)
- Features requiring external APIs or databases

## When Compacting

Always preserve: the list of files modified in this session, any failing test output, current SWE-bench results, and the user's latest instructions.

## Style

- Python 3.12+, type hints, pathlib over os.path
- Match existing patterns in the codebase
- No unnecessary abstractions — keep fixes minimal
- Always run syntax check after editing .py files
- Don't add features beyond what was asked
