# DryDock — Local CLI Coding Agent

**Repository:** https://github.com/fbobe321/drydock
**PyPI:** https://pypi.org/project/drydock-cli/ (v1.1.5)
**License:** Apache 2.0 (fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe))
**Status:** Active development — continuous improvement running

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
- `scripts/deploy_to_github.sh` — smoke tests → sync to GitHub (daily 4 AM)
- `scripts/publish_to_pypi.sh` — smoke tests → bump → build → PyPI → GitHub
- `scripts/backup.sh` — rsync to NAS (daily 3 AM)

**No mock tests.** All behavior testing uses the real vLLM backend. Mock tests gave false confidence — the `raw_arguments` crash ran for days because mocks never hit the real code path.

---

## Test Suite

| Tier | File | Tests | Backend | Time | When |
|------|------|-------|---------|------|------|
| **Smoke** | `test_smoke.py` | 20 | None | <1s | Every deploy |
| **Full Regression** | `test_full_regression.py` | 13 | Real vLLM | 5-10 min | Nightly 2 AM |

---

## Continuous Improvement

| System | Schedule | What it does |
|--------|----------|-------------|
| `continuous_bench.sh` | Always running | SWE-bench batches (20 tasks, 600s timeout) |
| `analyze_batch.py` | After each batch | Detects crash patterns, multi-file misses, test edits |
| `auto_fix.py` | After analysis | Applies safe prompt fixes based on patterns |
| `monitor_health.sh` | Every 30 min | Prunes worktrees, checks vLLM, disk space, pass rates |
| `deploy_to_github.sh` | Daily 4 AM | Smoke tests → push to GitHub |
| `backup.sh` | Daily 3 AM | rsync to NAS (192.168.50.183) |
| `@reboot` cron | On restart | Restarts bench loop after 2 min |

**Latest SWE-bench results (Mar 28):**
- 2,220/2,294 unique tasks tested (97%)
- Recent pass rate: **39-42%** (up from 17% baseline)
- Net improvement: **+22% pass rate** after bash abuse fix and nudge improvements

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

## Key Decisions

**No mock tests.** Every behavior test runs against the real vLLM backend. Mocks gave false confidence — critical bugs like `raw_arguments` crash and circuit breaker failures passed all mock tests but broke in production.

**Circuit breaker force-stops.** After 3 consecutive breaker fires, the conversation is terminated. The model was ignoring error messages and repeating — the only fix is cutting the loop.

**Bash abuse = #1 failure cause.** 88% of no-patch failures were the model running cat/grep/sed via bash instead of using search_replace. Fixed with escalating nudges at 5/8/12 bash calls.

**Nudges as user messages.** `_inject_system_note` buries nudges in old tool results where the model doesn't see them. Nudges now go as direct user messages.

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
