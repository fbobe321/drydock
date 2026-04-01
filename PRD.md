# DryDock — Local CLI Coding Agent

**Repository:** https://github.com/fbobe321/drydock
**PyPI:** https://pypi.org/project/drydock-cli/ (v2.0.0)
**License:** Apache 2.0 (fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe))
**Status:** Active development
**Current version:** 2.0.0
**Hardware:** 2x RTX 4060 Ti 16GB, devstral-24B-AWQ-4bit via vLLM at localhost:8000
**Server:** remus (Ubuntu 22.04, user: bobef)

---

## Deployment Process

Every change follows this pipeline:

1. **Code** → modify files in `drydock/` package
2. **Syntax check** → `python3 -c "import ast; ast.parse(...)"`
3. **Smoke tests** → 20 tests, <1s, no backend needed (imports, branding, safety, tools, skills, config)
4. **Commit** → descriptive message
5. **Publish** → `./scripts/publish_to_pypi.sh` (smoke tests → build → PyPI → GitHub)
6. **Full regression** → nightly at 2 AM, real vLLM backend, 13 tests

Scripts:
- `scripts/test_smoke.sh` — quick smoke tests (every deploy)
- `scripts/test_full.sh` — smoke + full regression (nightly)
- `scripts/test_bank.sh` — 83-test regression bank (10-18 hours, real vLLM)
- `scripts/deploy_to_github.sh` — smoke tests → sync to GitHub (daily 4 AM)
- `scripts/publish_to_pypi.sh` — smoke tests → bump → build → PyPI → GitHub
- `scripts/backup.sh` — rsync to NAS (daily 3 AM)

**No mock tests.** All behavior testing uses the real vLLM backend. Mock tests gave false confidence — the `raw_arguments` crash ran for days because mocks never hit the real code path.

---

## Configuration Management

**CRITICAL: Every script and cron MUST follow these rules.**

### Python Path
All scripts use explicit Python path, never bare `python3`:
```bash
export PATH="/home/bobef/miniconda3/bin:$PATH"
PYTHON="/home/bobef/miniconda3/bin/python3"
```
**Why:** Cron environment doesn't inherit shell PATH. Bare `python3` resolves to `/usr/bin/python3` (system Python, no packages). This caused an 8-hour crash loop where the test battery restarted every 10 minutes and immediately failed.

### Version Tracking
- `pyproject.toml` is the single source of truth for version
- `publish_to_pypi.sh` creates git tags (`v1.5.3`, etc.)
- `publish_to_pypi.sh` runs post-publish integration test (install in venv, verify version)
- Test scripts log version before running

### Cron Jobs (DryDock)
| Schedule | Script | Purpose |
|---|---|---|
| 2 AM daily | `test_full.sh` | Nightly regression (smoke + real backend) |
| 3 AM daily | `backup.sh` | rsync to NAS |
| 4 AM daily | `deploy_to_github.sh` | Sync code to GitHub |
| Every 10 min | `monitor_test_battery.sh` | Monitor/restart long test runs |

### Pre-Publish Checklist
1. All smoke tests pass (169 tests)
2. Version bumped in pyproject.toml
3. Build succeeds
4. Upload to PyPI
5. Git tag created
6. Integration test: install from wheel in temp venv, verify version matches
7. Deploy to GitHub

---

## File Locations (CRITICAL for session continuity)

```
/data3/drydock/                    ← Main repo (git)
├── drydock/                       ← Python package (source code)
│   ├── core/
│   │   ├── agent_loop.py          ← THE most important file. Loop detection, circuit breaker, auto-delegation
│   │   ├── prompts/cli.md         ← System prompt (debugging rules, tool usage, import rules)
│   │   ├── tools/builtins/        ← 24 builtin tools
│   │   ├── agents/                ← Subagent definitions (explore, diagnostic, planner)
│   │   └── skills/                ← Skill framework
│   ├── cli/textual_ui/app.py     ← TUI application
│   └── skills/                    ← 7 bundled skill markdown files
├── tests/
│   ├── test_smoke.py              ← 20 fast tests (imports, branding, safety)
│   ├── test_loop_detection.py     ← 38 loop/circuit breaker tests
│   ├── test_drydock_*.py          ← ~100 regression tests
│   ├── test_bank_prd.py           ← 15 PRD-driven tests (CORE usability tests)
│   ├── test_bank_prd_extended.py  ← 50 extended PRD tests (overnight battery)
│   ├── test_bank_build.py         ← 25 build tests
│   ├── test_bank_debug.py         ← 20 debug tests
│   ├── test_bank_update.py        ← 15 update tests
│   ├── test_bank_multiagent.py    ← 10 multi-agent tests
│   ├── test_bank_tools.py         ← 13 tool integration tests
│   └── testbank_helpers.py        ← Shared test infrastructure
├── scripts/
│   ├── deploy_to_github.sh        ← Clone GitHub, sync, push (daily 4 AM)
│   ├── publish_to_pypi.sh         ← Tests → build → PyPI → tag → integration test
│   ├── test_full.sh               ← Smoke + real backend regression (nightly 2 AM)
│   ├── test_bank.sh               ← 83-test bank runner with category selection
│   ├── monitor_test_battery.sh    ← Cron monitor: restart stuck tests, skip repeat failures
│   ├── backup.sh                  ← rsync to NAS (daily 3 AM)
│   └── install.sh                 ← User installation script
├── test_bank_results/             ← Test logs (overnight runs, partial results)
├── pyproject.toml                 ← Version, dependencies, build config
├── CLAUDE.md                      ← Instructions for AI assistants working on this repo
└── PRD.md                         ← This file
```

```
/data3/swe_bench_runs/             ← SWE-bench infrastructure (SEPARATE from drydock repo)
├── harness.py                     ← Runs drydock on SWE-bench tasks
├── continuous_bench.sh            ← Continuous improvement loop (CURRENTLY PAUSED)
├── analyze_batch.py               ← Post-batch analysis
├── auto_fix.py                    ← Auto-apply prompt fixes
└── monitor_health.sh              ← Worktree cleanup (CURRENTLY PAUSED)
```

```
~/.config/drydock/
├── github_token                   ← GitHub PAT for push/deploy
└── pypi_token                     ← PyPI API token for publishing
```

### Key Paths
- **vLLM server:** http://localhost:8000 (devstral-24B-AWQ-4bit)
- **NAS backup:** 192.168.50.183 via rsync
- **Conda env:** `/home/bobef/miniconda3/bin/python3` (ALWAYS use this, never bare `python3`)
- **Git remotes:** `drydock` → github.com/fbobe321/drydock, `origin` → github.com/mistralai/mistral-vibe
- **Main branch:** `main` (push to `drydock` remote, not `origin`)

---

## Test Suite

| Tier | File(s) | Tests | Backend | Time | When |
|------|---------|-------|---------|------|------|
| **Smoke** | `test_smoke.py` | 20 | None | <1s | Every deploy |
| **Loop Detection** | `test_loop_detection.py` | 38 | None | <1s | Every deploy |
| **Regression** | `test_drydock_*.py` | ~100 | None | ~1min | Every deploy |
| **Full Regression** | `test_full_regression.py` | 13 | Real vLLM | 5-10 min | Nightly 2 AM |
| **PRD Battery** | `test_bank_prd.py` | 15 | Real vLLM | ~20 min | On demand |
| **Extended PRD** | `test_bank_prd_extended.py` | 50 | Real vLLM | ~3-6 hours | Overnight |
| **Build/Debug/Update** | `test_bank_*.py` | 83 | Real vLLM | ~90 min | On demand |

**Run commands:**
```bash
# Quick smoke (every change)
python3 -m pytest tests/test_smoke.py tests/test_loop_detection.py -q

# All fast tests (before commit)
python3 -m pytest tests/test_drydock_regression.py tests/test_drydock_tasks.py tests/test_loop_detection.py tests/test_smoke.py -q

# PRD battery (user-realistic, needs vLLM)
python3 -m pytest tests/test_bank_prd.py -v -s

# Overnight (needs vLLM, 3-6 hours)
nohup python3 -m pytest tests/test_bank_prd.py tests/test_bank_prd_extended.py -v --tb=short > test_bank_results/run.log 2>&1 &
```

---

## SWE-bench (CURRENTLY PAUSED)

Infrastructure at `/data3/swe_bench_runs/` (not in drydock repo). Paused to focus on usability testing.

**Latest results (Mar 28):**
- 2,220/2,294 unique tasks tested (97%)
- Pass rate: **39-42%** (up from 17% baseline)
- Crons removed — re-enable with `continuous_bench.sh` when ready

---

## Tools (24 builtin)

| Tool | Description | Version |
|------|-------------|---------|
| `bash` | Shell execution, conda/pip support | Original |
| `grep` | Content search (ripgrep) | Original |
| `read_file` | Read files with offset/limit | Original |
| `write_file` | Create/overwrite files (blocks binary) | Original |
| `search_replace` | Edit files (blocks test files) | Original |
| `webfetch` | Fetch URLs | Original |
| `websearch` | DuckDuckGo search (no API key needed) | v0.8 |
| `ask_user_question` | Interactive questions | Original |
| `todo` | Todo list | Original |
| `task` | Delegate to subagent | Original |
| `exit_plan_mode` | Exit plan mode | Original |
| `glob` | Fast file pattern matching | v0.9 |
| `notebook_edit` | Edit Jupyter notebook cells | v0.9 |
| `task_create` | Create work item | v0.9 |
| `task_list` | List tasks | v0.9 |
| `task_update` | Update task status | v0.9 |
| `invoke_skill` | Model calls skills programmatically | v1.0 |
| `enter_worktree` | Git worktree isolation | v1.0 |
| `exit_worktree` | Return from worktree | v1.0 |
| `cron_create` | Schedule recurring prompt | v1.1 |
| `cron_list` | List scheduled crons | v1.1 |
| `cron_delete` | Delete cron | v1.1 |
| `tool_search` | Discover tools by keyword | v1.1 |
| `lsp` | Type checking, go-to-definition, find-references | v1.1 |
| `list_mcp_resources` | List MCP resources | v1.1 |
| `read_mcp_resource` | Read MCP resource | v1.1 |
| `powershell` | Windows/pwsh execution | v1.1 |

## Skills (7 bundled)

| Skill | Description | Version |
|-------|-------------|---------|
| `/create-presentation` | PowerPoint via python-pptx | v0.4 |
| `/deep-research` | Web + code research → report | v0.4 |
| `/investigate` | 3-strike debugging, scope lock, blast radius | v0.8 |
| `/review` | Two-pass code review, scope drift detection | v0.8 |
| `/ship` | Test → review → commit → push → PR pipeline | v0.8 |
| `/batch` | Apply same change across many files | v0.9 |
| `/simplify` | Three-pass code quality review | v0.9 |

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show shortcuts and commands |
| `/config` | Edit settings |
| `/clear` | Clear conversation |
| `/compact` | Summarize conversation to save context |
| `/consult` | Ask a smarter model for advice (in-context) |
| `/rewind` | Undo last assistant turn |
| `/status` | Show agent statistics |
| `/resume` | Browse and resume past sessions |

---

## Development Progress

### Phase 1-4 (Mar 14-23): Foundation
Baseline analysis, core agent improvements, crash elimination, conda/pip support, rebrand from Mistral Vibe.

### Phase 5 (Mar 24): UX Overhaul
Wave spinner, .drydock config, double Ctrl-C, --dangerously-skip-permissions, nautical Easter eggs, write timeouts, binary file guard, pptx skill, message queuing.

### Phase 6 (Mar 25): GSD + Performance
GSD-inspired: tiered context warnings, prompt injection guard, state file, deviation rules. Circuit breaker, thinking throttle, conda env protection, --insecure flag, /consult command.

### Phase 7 (Mar 26): Real Test-Driven Fixes
Shifted to TDD with real backend. Circuit breaker force-stop (was firing but model ignored it — 20 calls despite 17 breaker fires). Test file edits now blocked.

### Phase 8 (Mar 27): Analysis-Driven Improvements
- **88% of no-patch failures = bash abuse** (model uses cat/grep/sed instead of search_replace)
- Fix: bash abuse detection at 5/8/12 calls, force stop at 12
- Fix: nudges as user messages (not buried in old tool results)
- Multi-file check: prompt model to grep for related files after first edit
- DuckDuckGo websearch restored from user's GitHub changes
- All "vibe" references fixed (model name, logger, client metadata)

### Phase 9 (Mar 28): Feature Parity with Claude Code
Audit found 18 gaps vs Claude Code. All closed:

| Feature | What was built |
|---------|---------------|
| Glob tool | Fast file pattern matching |
| NotebookEdit | Jupyter cell editing |
| TaskCreate/List/Update | Interactive task lifecycle |
| Hook system | 6 events: PreToolUse, PostToolUse, SessionStart/End, PreEdit/PostEdit |
| InvokeSkill tool | Model calls skills programmatically |
| Worktree tools | Git worktree isolation (enter/exit) |
| CronCreate/List/Delete | Scheduled prompt execution |
| ToolSearch | Discover tools by keyword |
| LSP tool | Type checking, definition, references, symbols (pyright + grep fallback) |
| MCP Resources | List/read MCP server resources |
| PowerShell tool | Windows/pwsh support |
| Agent memory | Persistent per-agent memory across sessions |
| Per-agent model | Subagents can use different models |
| Markdown agents | Define agents in Markdown with YAML frontmatter |
| /batch skill | Apply changes across many files |
| /simplify skill | Three-pass code quality review |
| /rewind command | Undo last assistant turn |
| Skill infrastructure | context:fork, model selection, disable-model-invocation |

### Phase 9b (Mar 28): Scroll and Copy Fix
- `app.run(mouse=False)` — terminal handles mouse natively
- Text selection works for copy/paste
- Shift+Up/Down scrolls chat history
- Version sync: TUI reads from package metadata (no more hardcoded __version__)
- Mouse wheel scroll still doesn't work in Textual alternate screen mode — Shift+Up/Down is the workaround

---

## Architecture

```
drydock/
├── drydock/
│   ├── core/
│   │   ├── agent_loop.py          ← Main loop, circuit breaker, blast radius
│   │   ├── consultant.py          ← /consult backend (read-only advisor)
│   │   ├── hooks.py               ← Hook system (6 events)
│   │   ├── middleware.py          ← Tiered context warnings
│   │   ├── programmatic.py        ← Headless API
│   │   ├── session/
│   │   │   ├── state_file.py      ← Cross-session state persistence
│   │   │   └── agent_memory.py    ← Per-agent persistent memory
│   │   ├── tools/
│   │   │   ├── injection_guard.py ← Prompt injection detection
│   │   │   └── builtins/          ← 24 builtin tools
│   │   └── prompts/cli.md         ← System prompt
│   ├── cli/
│   │   ├── entrypoint.py          ← CLI flags (--insecure, --consultant, etc.)
│   │   ├── commands.py            ← Slash commands (/consult, /rewind, etc.)
│   │   └── textual_ui/app.py     ← TUI
│   └── skills/                    ← 7 bundled skills
├── tests/
│   ├── test_smoke.py              ← 20 tests, <1s (every deploy)
│   └── test_full_regression.py    ← 13 tests, real backend (nightly)
└── scripts/
    ├── deploy_to_github.sh
    ├── publish_to_pypi.sh
    ├── test_smoke.sh
    ├── test_full.sh
    └── backup.sh
```

---

## Mistral/devstral Tool Calling Design (CRITICAL)

Understanding how devstral handles tools, agents, and skills is essential. Getting this wrong cost weeks of debugging.

### How devstral Decides to Call Tools

In `tool_choice="auto"` (default), the model **autonomously decides** whether to call a tool or respond with text. Key rules:

1. **If the system prompt asks the model to output text first** (e.g., "Restate the goal"), the model generates text and SKIPS tool calling entirely
2. **Tool descriptions matter** — the model picks tools based on description quality, not just name
3. **`tool_choice="required"`** forces a tool call (useful for first-turn delegation)
4. **`tool_choice={"type":"function","function":{"name":"task"}}`** forces a specific tool

### The "Restate the Goal" Bug (v2.0.0 fix)

The single line `"Restate the goal in one line."` in the system prompt caused devstral to output text instead of making tool calls. This blocked subagent delegation for months. Evidence: removing it improved task() tool usage from 1/5 to 5/5 across SWE-bench prompts.

**Rule: Never ask the model to output text before its first tool call.** Any instruction that triggers text generation pre-empts tool calling.

### Tool Definition Format

```json
{
  "type": "function",
  "function": {
    "name": "task",
    "description": "Delegate to subagent (explore/diagnostic/planner)",
    "parameters": {
      "type": "object",
      "properties": {
        "task": {"type": "string"},
        "agent": {"type": "string", "enum": ["explore","diagnostic","planner"]}
      },
      "required": ["task", "agent"]
    }
  }
}
```

### Multi-Agent Pattern

DryDock's subagents follow Mistral's agent-to-agent delegation pattern:
- `explore` — read-only codebase exploration (grep, read_file, glob)
- `diagnostic` — analyze test failures (grep, read_file, bash, glob)
- `planner` — create implementation plans (grep, read_file, glob)

Each spawns a separate AgentLoop with its own context window. The main agent sees results as tool responses.

### Build Orchestrator (bypasses model entirely)

For complex build tasks, the orchestrator makes direct API calls instead of going through the model:
- Phase 1: Deterministic plan extraction from PRD (no LLM)
- Phase 2: Scaffold package structure (no LLM)
- Phase 3: One `backend.complete()` call per file (separate context each)
- Phase 4: Model tests and fixes (standard agent loop)

### Key Gotchas

| Issue | Solution |
|-------|----------|
| Model outputs text instead of calling tools | Don't ask for text before first tool call |
| vLLM rejects `user` after `tool` messages | `_sanitize_message_ordering()` before every LLM call |
| `tool_choice="any"` not supported by vLLM | Use `"required"` instead |
| Model hallucinates tool names (`task_agent:`) | Error handler shows available tool names |
| Circuit breaker blocks valid retries | Only block FAILED commands (3+ identical failures) |
| Separate httpx calls get 404 from vLLM | Use `self.backend.complete()` (same connection) |

### Upstream Alignment (Mistral Vibe docs)

DryDock is a fork of mistralai/mistral-vibe. The upstream docs at docs.mistral.ai/mistral-vibe/ define:

| Feature | Upstream | DryDock Status |
|---|---|---|
| Agent `.toml` profiles | `~/.vibe/agents/` | `~/.drydock/agents/` |
| Skills with `SKILL.md` | `~/.vibe/skills/` + project + config | Same (7 bundled + user) |
| `task` tool → subagents | explore, diagnostic, planner | Same (v2.0.0 fixed delegation) |
| `AGENTS.md` / `VIBE.md` | Project-root instructions loaded into context | Now loads AGENTS.md, DRYDOCK.md, .drydock.md |
| `DRYDOCK.md` | (our addition) | Per-project instructions |
| `.drydock/rules/*.md` | (our addition) | Modular project rules |
| Skill enable/disable | Glob + regex patterns | Same |
| Agent handoffs | Cloud-only (agent-to-agent) | Not applicable (local) |
| Cloud Agents API | `client.beta.agents` | Not used (local-first) |

**Per-Project Instructions (loaded into system prompt):**
- `AGENTS.md` — upstream standard, works in workspace root
- `DRYDOCK.md` — our addition, same purpose
- `.drydock/rules/*.md` — modular rules, all loaded

**What we DON'T use from upstream:**
- Mistral Cloud Agents API (requires mistral-medium/large, cloud-only)
- Agent handoffs (cloud orchestration)
- Teleport to Vibe Nuage (cloud feature)

---

## Key Decisions

**No mock tests.** Every behavior test runs against the real vLLM backend. Mocks gave false confidence — critical bugs like `raw_arguments` crash and circuit breaker failures passed all mock tests but broke in production.

**Circuit breaker: failed-only.** Only blocks commands that FAILED 3+ times with identical args. Successful commands and retries-after-fix are never blocked. Loop detection handles pattern-based repetition separately.

**Bash abuse detection.** Model uses cat/grep/sed via bash instead of proper tools. Detected at 3/6/10 bash calls with escalating nudges.

**Auto-explore for all tasks.** Instead of relying on the model to call `task(agent="explore")`, the agent loop auto-reads project files and injects function/class signatures before the model's first turn.

**Scroll workaround.** Mouse wheel doesn't work in Textual's alternate screen buffer. Shift+Up/Down is the working solution. Copy/paste works with native text selection (mouse=False).

---

## Lessons Learned

1. **Mock tests are dangerous.** They pass when real code is broken.
2. **Tests must fail first.** Write the test, watch it fail, then fix the code.
3. **The model ignores warnings.** Only hard stops (circuit breaker, force exit) actually prevent loops.
4. **Users find different bugs than benchmarks.** SWE-bench finds agent logic bugs. Real usage finds UI/UX bugs.
5. **Fix the #1 failure mode.** Bash abuse caused 88% of failures — one fix doubled the pass rate.
6. **I can't test the TUI.** I write code changes based on docs but can't verify visually. The user is the only tester for UI.

### Phase 10 (Mar 28): Multi-Agent Actually Works

The agent was NOT using subagents despite having them available since Phase 2.
Every previous "fix" added code but never told the model WHEN to use it.

**Test result (real backend, no hint in prompt):**
- Before fix: FAILED — 0 subagent calls for 5-file project review
- After fix: PASSED — model naturally calls `task(agent="explore")`

**Root cause:** System prompt had no instructions about multi-agent delegation.
Adding Python tools/agents doesn't matter if the prompt doesn't tell the model to use them.

**Fix:** Added comprehensive "Multi-Agent Delegation" section to system prompt:
- Lists all available agents (explore, diagnostic, planner)
- Lists all available skills (investigate, review, ship, batch, simplify, deep-research)
- Shows exact syntax for delegation
- Explicit WHEN to delegate rules (3+ files, reviews, bugs, planning)
- WHEN NOT to delegate (simple fixes, quick questions)

### Phase 11 (Mar 28): Agent Loop Hardening + 83-Test Regression Bank

User reported DryDock was unusable — model looped 12+ times running the same command, search_replace blocked legitimate files, basic tasks couldn't complete.

**Agent loop fixes (5 bugs):**
- Empty model response: retry 3x with nudge (model sometimes returns nothing)
- Invalid tool name recovery: model hallucinates `task_agent:`, now gets correct tool list
- Bash traceback guidance: extracts file:line from crash, directs model to read source
- Circuit breaker for successful repeats: blocks after 4 identical bash calls (was unlimited)
- Tighter loop detection: warning at 4 (was 8), force-stop at 8 (was 25)

**System prompt improvements:**
- Added debugging best practices: RUN → READ → FIX → VERIFY cycle
- Common bug pattern hints (TypeError, KeyError, IndexError, off-by-one)

**83-test regression bank (real vLLM backend, ~90 min):**

| Suite | Tests | Pass Rate |
|---|---|---|
| BUILD (easy/medium/hard) | 25 | 100% |
| DEBUG (easy/medium/hard) | 20 | 75% |
| UPDATE (easy/medium/hard) | 15 | 100% |
| MULTIAGENT | 10 | 100% |
| TOOLS | 13 | 100% |
| **Total** | **83** | **94%** |

Run with: `./scripts/test_bank.sh` (full) or `./scripts/test_bank.sh quick` (easy only, ~2h)

**Removed:** False-positive test file guard in search_replace that blocked any file in a directory containing "test" in its name.

### Phase 12 (Mar 29): Configuration Management + Auto-Delegation + PRD Tests

**Config management crisis:** Overnight test battery crashed for 8 hours because cron used system Python (no pytest). All scripts now use explicit `/home/bobef/miniconda3/bin/python3`.

**Auto-delegation:** Model wasn't using subagents despite prompt instructions. Fixed by injecting context automatically before the model's first turn:
- Project file listing (replaces explore subagent)
- Skill content injection (matches prompt keywords to skills)
- Planning prompt for complex builds (forces absolute imports, python3 -m)

**Circuit breaker reset:** After write_file/search_replace, circuit breaker history clears. Previously-failing commands can be retried after code changes.

**Post-success stop:** After 3 successful bash runs, model is told to stop testing and summarize.

**PRD-driven test bank:** 65 tests (15 core + 50 extended) that give DryDock a PRD and verify the built project RUNS. Core 15 pass at 100%.

**Relative import auto-fix:** When model gets "relative import with no known parent package", agent loop explains absolute imports vs python3 -m and forces search_replace.

### Phase 13 (Mar 29-30): Multi-Phase Build Orchestrator (v1.6.0-v1.8.0)

DryDock was unusable for building projects — broke within 2 minutes every time. Root causes:
1. Model wasted context on `__init__.py`, relative imports, `__main__.py`
2. Single context window for everything → model degraded by Phase 4
3. Cross-file import name mismatches (each file built independently)

**Solution: Multi-phase build orchestrator** (`drydock/core/build_orchestrator.py`):

| Phase | What | LLM? |
|---|---|---|
| 1. PLAN | Parse PRD, extract package name + modules | No (regex) |
| 2. SCAFFOLD | Create dirs, `__init__.py`, `__main__.py` | No (deterministic) |
| 3. IMPLEMENT | One `backend.complete()` call per file | Yes (separate context each) |
| 3.5 FIX | Cross-file import matching, circular import breaking, `from __future__ import annotations` | No (AST analysis) |
| 4. TEST | Model runs `python3 -m package --help`, fixes errors | Yes (main loop) |

**Results:** 79-83% pass rate on 6 PRD types over 24 rounds of automated testing.

**Key issues solved along the way:**
- Orchestrator was calling Mistral cloud API instead of local vLLM (wrong provider selection)
- Separate httpx calls got 404 from vLLM → switched to `self.backend.complete()`
- Plan extractor matched English words as package names ("with", "from") → 80+ word blocklist
- Model rebuilt files in Phase 4 despite being told not to → yield summary directly to user
- Circuit breaker blocked valid retries after fixes → disabled for successful commands

### Phase 14 (Mar 30): Subagent Delegation Fixed + v2.0.0

**ROOT CAUSE FOUND for subagent non-use:** The system prompt line `"Restate the goal in one line."` caused devstral to output text instead of making tool calls. This pre-empted the tool-calling mechanism entirely.

Evidence: removing this one line improved `task(agent="explore")` usage from 1/5 to 5/5 across SWE-bench test prompts.

**Other v2.0.0 changes:**
- All user-visible "Vibe" references removed (interrupt, window title, onboarding, user agent)
- `/setup-model` command: interactive wizard for local LLM setup (vLLM, Ollama, LM Studio)
- `--local` CLI flag: `drydock --local http://localhost:8000/v1`
- Auto-explore + skill routing for all task types (review, investigate, ship, simplify)
- Telegram release notifications
- `whats_new.md` updated

### Phase 15 (Mar 31): Meta-Harness Framework + SWE-bench Push

**Paper:** "Meta-Harness: End-to-End Optimization of Model Harnesses" (arXiv:2603.28052)
**Key insight:** Optimize the harness around the model, not the model itself. A harness change gave +18pp on TerminalBench-2.

**What we implemented from Meta-Harness:**

1. **Environment bootstrapping** — Single compound shell command before first LLM call gathers:
   - Repo structure (`find . -name "*.py" | head -30`)
   - Python version, test framework
   - Top-level directories, recent commits
   - Injected as `[Environment Snapshot]` in the prompt
   - Eliminates 2-4 wasted exploratory turns

2. **AGENTS.md per SWE-bench task** — Auto-created in each worktree with bug-fix workflow instructions

3. **Additive-only changes** — Paper proved that modifying control flow regresses. Only ADD context before the agent loop. This validates our decision to disable the orchestrator and circuit breaker.

**SWE-bench status:**
- Baseline (v2.1.3 + AGENTS.md): 4/10 file matches (40%)
- 50-task eval running with env bootstrapping
- Previous "39-42% pass rate" was file-match only — actual test pass rate was 0%
- Target: 68% (devstral-small-2 24B, per Mistral's published results)
- Mistral achieved 68% with Vibe CLI + Cline harness

**Meta-Harness techniques still to implement:**
- Double-confirmation before completion (verify checklist)
- Execution trace logging for systematic failure diagnosis
- Repo-specific routing (different strategies per SWE-bench repo)
- Draft-then-verify for patches
- Contrastive examples for decision boundaries

**Reference:** https://github.com/stanford-iris-lab/meta-harness-tbench2-artifact

### Lessons Learned (Updated)

1. **One prompt line can block an entire feature.** "Restate the goal" prevented tool calling for months.
2. **AGENTS.md is essential for devstral.** Without per-project instructions file, model loops on ls/bash. Auto-created on first session.
3. **Additive-only harness changes work best.** Every control flow modification (circuit breaker, orchestrator, nudges) caused regressions. Just inject better context.
4. **The harness matters as much as the model.** Meta-Harness paper shows 6x performance gap from harness changes alone.
5. **Environment bootstrapping saves turns.** Pre-gathering repo structure/versions eliminates 2-4 wasted exploration turns.
6. **Plan→Edit workflow is Mistral's design.** Use plan mode first, then switch to accept-edits. Don't try to do everything in one agent shot.
7. **Same-process API calls can fail.** httpx from inside DryDock got 404 from vLLM. Use the existing backend connection.
8. **Test the installed package, not source.** Different Python envs (miniconda3 vs miniforge3) have different behavior.
9. **Explicit Python paths everywhere.** Cron doesn't inherit PATH. Every script must use the full path.

### Phase 16 (Mar 31): No More Session Kills + Meta-Harness Iteration

**v2.2.0-2.2.1: Loop guidance overhaul**
- ALL session-killing behavior removed from loop detection
- Loop detection now only prunes duplicates and injects gentle nudges
- API errors auto-retry after 10s instead of stopping
- Queue shows actual command text
- Only hard stop: MAX_TOOL_TURNS (200)

**SWE-bench 50-task eval (v2.1.3 + env bootstrap):**
- File matches: 15/50 (30%)
- Patches generated: 19/50 (38%)
- Strong on Django, weak on sympy/sphinx
- 35/50 tasks produced NO patch — model explores but doesn't commit

**Next: Meta-Harness iteration cycle**
- Analyze the 35 no-patch failures
- Improve prompt to be more directive ("you MUST make an edit")
- Add repo-specific strategies
- Implement draft-then-verify pattern
- Target: 68% (devstral-small-2 published score)
