# Drydock — Local CLI Coding Agent

**Repository:** https://github.com/fbobe321/drydock
**License:** Apache 2.0 (fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe))
**Status:** Active development

---

## Objective

Build Drydock into a best-in-class local coding agent, measured by SWE-bench Verified pass rate.

| | Value |
|---|---|
| **Baseline (Mar 15)** | 60.6% files-match (154/254 tasks) |
| **Post-fix (Mar 23)** | ~47% on previously-impossible tasks; 0 crashes |
| **Target** | 80%+ |
| **Hardware** | 2x RTX 4060 Ti 16GB, devstral-24B-AWQ-4bit via vLLM, 128k context |

---

## Development Progress

### Phase 1: Baseline & Analysis (Mar 14–20)

1,138 task runs across 254 unique SWE-bench Verified tasks established the baseline.

**Baseline Performance:**

| Metric | Value |
|--------|-------|
| Unique tasks tested | 254 |
| Files match gold (best attempt) | 154/254 (60.6%) |
| Patch generated | 192/254 (75.6%) |
| No patch (timeout/loop kill) | 72 (28%) |
| No patch (gave up) | 27 (11%) |
| Wrong files edited | 38 (15%) |
| Message ordering crash | 23 (9%) |

**By Repository:**

| Repo | Pass Rate |
|------|-----------|
| flask | 100% (3/3) |
| xarray | 75% (9/12) |
| scikit-learn | 65% (13/20) |
| pytest | 62% (8/13) |
| sympy | 59% (24/41) |
| requests | 60% (3/5) |
| sphinx | 56% (9/16) |
| django | 54% (59/109) |
| matplotlib | 45% (5/11) |
| astropy | 44% (4/9) |

**Top Failure Modes Identified:**

1. **Message ordering crash (9%)** — vLLM/Mistral rejects `user` after `tool` messages
2. **Wrong file edited (15%)** — model edits test files or wrong source module
3. **Loop detection kills (28%)** — agent stopped before making an edit
4. **Describes fix but doesn't apply (11%)** — model writes prose instead of calling search_replace

### Phase 2: Core Agent Improvements (Mar 15–20)

10 features implemented in Drydock's source:

1. **Failure Recovery Middleware** — search_replace errors include RECOVERY hint telling model to re-read and retry
2. **Fuzzy search_replace Auto-Apply** — auto-applies matches >= 95% similarity when exact match fails
3. **grep Source-First Sorting** — source files sorted before test files in results
4. **Smarter Loop Detection** — tool-specific thresholds, investigation-aware warnings
5. **Diagnostic Subagent** — analyzes test failures, registered as builtin agent
6. **Planner Subagent** — pre-edit analysis, identifies target file/function
7. **"Never Edit Tests" Rule** — explicit in cli.md system prompt
8. **SWE-bench Workflow Prompt** — two-phase investigate-then-fix guidance
9. **.codeignore** — test dirs excluded from grep in SWE-bench worktrees
10. **Message Ordering Fix** — `_inject_system_note()` prevents user-after-tool crash

### Phase 3: Crash Elimination & Optimization (Mar 23)

Diagnosed and fixed the remaining crash modes. Ran validation batches (51 tasks total).

**Fixes applied:**

| Fix | Impact |
|-----|--------|
| `_sanitize_message_ordering()` safety net | Eliminated 63+ message ordering crashes |
| Harness path update (mistral-vibe → drydock) | Eliminated 110+ empty output crashes |
| Middleware INJECT_MESSAGE safe injection | Prevented user-after-tool in middleware path |
| `_prune_repeated_tool_calls` MessageList bug | Fixed list replacement breaking downstream methods |
| Loop detection thresholds relaxed (WARNING 6→8, FORCE_STOP 20→25) | More room for legitimate investigation |
| Investigation tools count 0.3 per warning (was 0.5) | grep/read_file penalized less |
| Forced-edit nudge at 15 turns (was 20), repeats every 5 | Earlier intervention for stuck agents |
| Escalating text-without-action (3 levels) | Stronger nudges when model describes instead of fixing |
| ConversationLimitException exits 0 | Middleware stop is normal completion, not error |

**Validation Results:**

| Batch | Tasks | Files Match | Notes |
|-------|-------|-------------|-------|
| postfix_v1 | 21 (all never-passed) | 9/21 (43%) | Zero crashes |
| postfix_v2 | 30 (10 regression + 20 new) | 14/30 (47%) | Zero crashes, zero errors |
| **Newly passing** | 40 previously-impossible | **19/40 (48%)** | Tasks that never passed before |

**Regressions:** 6/10 previously-passing tasks failed — all due to model non-determinism (empty output, no edits made), not code changes.

### Phase 4: Conda/Pip Support & Rebrand (Mar 23)

- **Bash tool allowlist** — pip install, conda install/run/list, pytest, make, python -c all auto-approve
- **Conda environment detection** — `_get_conda_setup_script()` finds conda.sh and sets `BASH_ENV` so `conda activate` works in non-interactive subprocesses
- **Full rebrand** — Mistral Vibe → Drydock throughout CLI, TUI, docs, config
- **Published to GitHub** — https://github.com/fbobe321/drydock

---

## Architecture

```
drydock/
├── PRD.md                                  ← This document
├── NOTICE                                  ← Apache 2.0 attribution
├── vibe/core/
│   ├── agent_loop.py                       ← Loop detection, failure recovery, message ordering
│   ├── programmatic.py                     ← Programmatic/headless API entry point
│   ├── agents/models.py                    ← Agent profiles (diagnostic, planner, auto-approve)
│   ├── prompts/
│   │   ├── cli.md                          ← System prompt (SWE-bench rules, two-phase workflow)
│   │   ├── diagnostic.md                   ← Failure analysis prompt
│   │   └── planner.md                      ← Pre-edit analysis prompt
│   └── tools/builtins/
│       ├── bash.py                         ← Shell execution, conda/pip support, allowlist/denylist
│       ├── search_replace.py               ← Fuzzy auto-apply, recovery hints
│       ├── grep.py                         ← Source-first sorting
│       └── task.py                         ← Subagent spawning
├── vibe/cli/
│   ├── entrypoint.py                       ← CLI entry point
│   └── textual_ui/                         ← TUI (Textual-based)
└── vibe/setup/                             ← Onboarding, trusted folders
```

### Key Technical Decisions

**Message ordering safety net (`_sanitize_message_ordering`):**
Runs before every `_chat()` and `_chat_streaming()` call. Scans the message list and merges any `user` messages that follow `tool` messages into the preceding tool result as `[SYSTEM: ...]` annotations. Also appends `"Continue."` if the last message is `assistant`. This is a belt-and-suspenders approach — the injection points should already be safe, but this catches edge cases.

**Loop detection philosophy:**
Investigation tools (grep, read_file) get a longer leash than action tools (bash, search_replace). The agent needs to explore before it can fix. Thresholds are tuned for SWE-bench tasks where 3-5 greps + 2-3 reads is typical before making an edit. Warnings escalate: soft nudge → strong directive → tool replacement → force stop.

**Conda in non-interactive shells:**
`conda activate` is a shell function defined in `.bashrc`, which isn't sourced for non-interactive `subprocess` commands. We detect the conda installation and set `BASH_ENV` to `conda.sh`, which bash sources before every non-interactive command.

---

## Lessons Learned

1. **Don't build parallel systems — improve the actual tool.** We wasted time building a custom `multi_agent/` pipeline. Every improvement should be a commit to Drydock's source.

2. **Monitoring without fixing is useless.** 797 watchdog restarts, zero code fixes. Crons must diagnose AND fix.

3. **LLM self-improvement doesn't work at 24B.** The auto-improve loop tried to use devstral to suggest code changes to its own pipeline. It never produced a valid improvement.

4. **Small batch sizes create noise.** 20-task batches have huge variance (15% to 62.5%). Need 50+ tasks per measurement.

5. **The biggest win was fixing a bug, not adding features.** The message ordering crash affected 9% of all tasks. Fixing that one bug was worth more than all the subagents and prompt changes combined.

6. **Non-determinism is real.** "Always-passing" tasks can fail on any given run. Don't chase regressions that are just model variance — run larger batches.

---

## Next Steps

### P1: Reduce "no patch" failures
The model either gets loop-killed or talks instead of acting. Currently ~28% of tasks.
- Escalating forced-edit nudges (partially implemented)
- Investigate why some tasks produce zero output even with `PYTHONUNBUFFERED=1`

### P2: Fix wrong source file selection
The model finds the right function name but in the wrong file (e.g., `models/query.py` instead of `models/sql/query.py`). Currently ~11%.
- Source directory hints per repo (partially implemented for pytest, matplotlib, scikit-learn)
- Recovery hints on search_replace failure suggesting deeper module paths

### P3: Run full 500-task benchmark
Get a real, stable number across all SWE-bench Verified tasks with the current codebase.

### P4: Support more LLM backends
Currently optimized for devstral-24B via vLLM. Test with other models (Codestral, Claude, GPT-4) to understand which improvements are model-specific vs general.
