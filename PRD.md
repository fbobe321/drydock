# DryDock — Local CLI Coding Agent

**Repository:** https://github.com/fbobe321/drydock
**PyPI:** https://pypi.org/project/drydock-cli/ (v0.6.3)
**License:** Apache 2.0 (fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe))
**Status:** Active development — continuous improvement running

---

## Deployment Process

Every change follows this pipeline:

1. **Code** → modify files in `drydock/` package directory
2. **Syntax check** → `python3 -c "import ast; ast.parse(...)"`
3. **Regression tests** → 81 tests must pass (auto-run by deploy scripts)
4. **Commit** → descriptive message with Co-Authored-By
5. **Publish** → `./scripts/publish_to_pypi.sh` (tests → build → PyPI → GitHub)

Scripts:
- `scripts/deploy_to_github.sh` — runs tests, syncs to GitHub. Cron daily at 4 AM.
- `scripts/publish_to_pypi.sh` — runs tests, bumps version, builds wheel, uploads to PyPI, deploys to GitHub. Aborts on test failure.

Both scripts gate on the full 81-test regression suite. No deploy happens if tests fail.

---

## Regression Test Suite (81 tests)

Two test files, run in 0.3s:

```bash
pytest tests/test_drydock_regression.py tests/test_drydock_tasks.py -p no:xdist -p no:cov --override-ini="addopts="
```

**test_drydock_regression.py (37 tests):**
Message ordering (6), system note injection (3), wave spinner (2), config paths (3), state terms (4), Easter eggs (2), bash allowlist (5), conda detection (2), CLI flags (2), loop thresholds (1), write file safety (3), loop patterns (2), loading widget (2)

**test_drydock_tasks.py (44 tests):**
Binary file guard (5), unknown tool handling (1), loop thresholds (3), file I/O timeouts (4), skill discovery (3), config migration (2), system prompt content (3), bash allowlist (2), wave spinner (3), Easter eggs (1), injection guard (4), state file (4), context warnings (3), deviation rules (1), circuit breaker (2), CLI flags (2), thinking throttle (1)

---

## Continuous Improvement

DryDock improves itself automatically and survives restarts:

1. **`continuous_bench.sh`** runs SWE-bench batches in a loop (20 tasks, 600s timeout)
2. **`@reboot` cron** restarts 2 minutes after any system restart
3. **Every 6 hours** cron re-launches if the loop died
4. **Daily at 4 AM** deploys to GitHub (with test gate)
5. **State persists** in `continuous_bench_state.json`

**Latest results (Mar 25):**
- 520 task runs completed, 500/500 unique tasks covered
- **254/500 passed (50.8%)** — up from 207/500 baseline (41.4%)
- **+47 net improvement** (117 newly passing, 70 regressions from model non-determinism)

---

## Objective

| | Value |
|---|---|
| **Baseline (Mar 15)** | 207/500 (41.4%) |
| **Current (Mar 25)** | 254/500 (50.8%) |
| **Net improvement** | +47 tasks (+9.4%) |
| **Target** | 80%+ |
| **Hardware** | 2x RTX 4060 Ti 16GB, devstral-24B-AWQ-4bit via vLLM, 128k context |

---

## Development Progress

### Phase 1: Baseline & Analysis (Mar 14–20)
1,138 task runs across 254 unique SWE-bench Verified tasks. Identified top failure modes: message ordering crashes (9%), wrong file edits (15%), loop kills (28%), prose-only responses (11%).

### Phase 2: Core Agent Improvements (Mar 15–20)
10 features: failure recovery middleware, fuzzy search_replace, grep source-first sorting, smarter loop detection, diagnostic/planner subagents, "never edit tests" rule, SWE-bench workflow prompt, .codeignore, message ordering fix.

### Phase 3: Crash Elimination (Mar 23)
`_sanitize_message_ordering()` safety net, middleware safe injection, MessageList bug fix, loop threshold tuning, forced-edit nudges, ConversationLimitException exit 0. Result: zero crashes.

### Phase 4: Conda/Pip & Rebrand (Mar 23)
Bash tool allowlist (pip, conda, pytest auto-approve), conda environment detection via BASH_ENV, full Mistral Vibe → DryDock rebrand. Published to GitHub.

### Phase 5: UX Overhaul (Mar 24)
Wave spinner, .drydock config dir, double Ctrl-C, --dangerously-skip-permissions, nautical Easter eggs, status throttle, write file timeouts, binary file guard, pptx skill, bash abuse detection, alternating loop detection, progressive budget warnings, ambiguous prompt guard, message queuing, mouse scroll, ocean blue onboarding. 76 regression tests gating deploys.

### Phase 5b: Package Rename (Mar 24)
`vibe/` → `drydock/` directory rename (908 imports, 257 files). Published to PyPI as drydock-cli. Removed `vibe` CLI entry point.

### Phase 6: GSD Integration & Performance (Mar 25)

Inspired by [get-shit-done](https://github.com/gsd-build/get-shit-done) (41k stars):

| Feature | Details |
|---------|---------|
| Tiered context warnings | 4 levels at 50/65/75/85% usage, debounced every 5 calls |
| Prompt injection guard | Detects role overrides, invisible Unicode, hidden instructions |
| Structured state file | `.drydock/state.md` persists task context across sessions |
| Deviation handling rules | Auto-fix bugs/imports, ask user for architecture/scope decisions |
| Circuit breaker | Blocks exact same tool call after 2 attempts with "already attempted" summary |
| Thinking flicker fix | Status words change every 4s, not every token |
| Conda env protection | Preserves user's active environment in subprocesses |
| `--insecure` / `-k` flag | Disables SSL verification for corporate proxies |
| `/consult` command | Ask a smarter model for advice — response visible to local model |
| `consultant_model` config | Select consultant from configured models in config.toml |
| `.vibe` auto-migration | Copies ~/.vibe → ~/.drydock on first run |

---

## Architecture

```
drydock/
├── PRD.md                                  ← This document
├── NOTICE                                  ← Apache 2.0 attribution
├── drydock/
│   ├── core/
│   │   ├── agent_loop.py                   ← Loop detection, circuit breaker, message ordering
│   │   ├── consultant.py                   ← /consult command backend (read-only advisor)
│   │   ├── middleware.py                   ← Tiered context warnings
│   │   ├── programmatic.py                 ← Headless API entry point
│   │   ├── session/state_file.py           ← Cross-session state persistence
│   │   ├── tools/injection_guard.py        ← Prompt injection detection
│   │   ├── tools/builtins/bash.py          ← Shell, conda/pip, allowlist/denylist
│   │   ├── tools/builtins/search_replace.py ← Fuzzy auto-apply, recovery hints
│   │   └── prompts/cli.md                  ← System prompt with deviation rules
│   ├── cli/
│   │   ├── entrypoint.py                   ← CLI flags (--insecure, --consultant, etc.)
│   │   ├── commands.py                     ← Slash commands (/consult, /help, etc.)
│   │   └── textual_ui/app.py              ← TUI, message queuing, double Ctrl-C
│   └── skills/
│       └── create-presentation/SKILL.md    ← Bundled pptx skill
├── tests/
│   ├── test_drydock_regression.py          ← 37 component tests
│   └── test_drydock_tasks.py               ← 44 behavior tests
└── scripts/
    ├── deploy_to_github.sh                 ← Test-gated GitHub deploy
    └── publish_to_pypi.sh                  ← Test-gated PyPI publish
```

---

## Key Technical Decisions

**Circuit breaker:** Tracks tool call signatures (hash of name + args). After 2 identical calls, blocks execution and returns the cached result with "ALREADY ATTEMPTED" summary and suggestions to try different approaches.

**Consultant model:** `/consult` sends a question to a configured model using DryDock's own backend (same providers, same API keys). The consultant never calls tools — it only returns text advice that gets injected into the conversation so the local model can see and act on it.

**Tiered context warnings:** 4 warning levels (50%, 65%, 75%, 85% context used). Debounced every 5 tool calls. Messages escalate from "wrap up" to "STOP NOW."

**Config migration:** On first run, if `~/.vibe` exists but `~/.drydock` doesn't, auto-copies everything and leaves a `MIGRATED.txt` note.

---

## Lessons Learned

1. **Fix bugs before adding features.** The message ordering crash fix was worth more than all subagents combined.
2. **Non-determinism is real.** Run 500+ tasks to get stable numbers. 20-task batches are noise.
3. **The model needs hard guardrails, not suggestions.** The circuit breaker (block after 2) works better than warnings (which the model ignores).
4. **Test everything.** 81 regression tests catch issues before they ship. Gate deploys on tests.
5. **Users find different bugs than benchmarks.** SWE-bench found crash bugs. Real usage found UX bugs (flicker, loops, wrong config dirs).

---

## Next Steps

### P1: Startup optimization
20-second delay on launch. Profile imports, lazy-load heavy modules.

### P2: Expand to full SWE-bench (2,294 tasks)
Batch file ready. Switch continuous_bench.sh once Verified is stable.

### P3: Task queue UI
Show the user what the agent has planned in its execution pipeline.

### P4: Support more LLM backends
Test with Claude, GPT-4, Gemini to understand which improvements are model-specific.

### Phase 7: Real Test-Driven Fixes (Mar 26)

Shifted to test-driven development with real vLLM backend — no more mocks for critical bugs.

| Issue | Test Result (before fix) | Fix | Test Result (after fix) |
|-------|------------------------|-----|----------------------|
| Circuit breaker fires but model keeps calling | FAILED: 20 bash calls, 17 breaker fires ignored | Force-stop conversation after 3 consecutive breaker fires + break tool loop | PASSED: stops within 3 calls |

**Testing methodology changed:**
- All critical tests run against real vLLM at localhost:8000
- Tests must FAIL first (proving the bug exists)
- Then fix code, re-run until PASS
- 166 total tests (155 mock + 11 real backend)
