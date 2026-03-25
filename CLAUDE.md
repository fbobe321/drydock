# Drydock Development Guide

## Project

Drydock is a local CLI coding agent (fork of mistral-vibe, Apache 2.0).
- **Repo:** https://github.com/fbobe321/drydock
- **Goal:** Best-in-class SWE-bench Verified pass rate with local LLMs
- **Hardware:** 2x RTX 4060 Ti 16GB, devstral-24B-AWQ-4bit via vLLM at localhost:8000

## Build & Test

```bash
# Install
uv sync

# Run drydock CLI
uv run drydock

# Run programmatically (headless, for benchmarks)
python3 -c "import sys; sys.path.insert(0, '.'); from vibe.cli.entrypoint import main; main()" --agent auto-approve -p "your prompt"

# Run tests (note: pytest from swe-bench worktrees can pollute; use -p no:conftest if needed)
uv run pytest tests/ -x -q --timeout=30

# Syntax check modified files
python3 -c "import ast; ast.parse(open('path/to/file.py').read())"
```

## Key Architecture

- `drydock/core/agent_loop.py` — Main agent loop. Loop detection, message ordering, tool execution. **Most changes go here.**
- `drydock/core/programmatic.py` — Headless API entry point used by SWE-bench harness
- `drydock/core/tools/builtins/bash.py` — Shell tool with allowlist/denylist, conda support
- `drydock/core/tools/builtins/search_replace.py` — File editing tool with fuzzy matching
- `drydock/core/prompts/cli.md` — System prompt (two-phase workflow, SWE-bench rules)
- `drydock/core/types.py` — `MessageList` (custom Sequence, use `.reset()` not `=[]`)

## Critical Constraints

- **MessageList is not a plain list.** Never `self.messages = [...]`. Use `self.messages.reset([...])`.
- **vLLM/Mistral rejects `user` after `tool` messages.** `_sanitize_message_ordering()` runs before every LLM call as safety net. All injection should use `_inject_system_note()`.
- **`os._exit()` in programmatic.py** — necessary because async cleanup hangs. Ensure stdout is flushed before calling.
- **Tests may fail due to stale SWE-bench worktrees** poisoning pytest. Use `python3 -c "import ast; ..."` for syntax checks instead.

## SWE-bench Infrastructure

Benchmarking scripts live at `/data3/swe_bench_runs/` (not in the drydock repo):
- `harness.py` — Runs drydock on SWE-bench tasks, captures patches
- `continuous_bench.sh` — Continuous improvement loop (cron @reboot + every 6h)
- `deploy_to_github.sh` → `/data3/drydock/scripts/` — Daily push to GitHub

## When Compacting

Always preserve: the list of files modified in this session, any failing test output, and the current task context from the PRD.

## Style

- Python 3.12+, type hints, pathlib over os.path
- Match existing patterns in the codebase
- No unnecessary abstractions — keep fixes minimal
- Always run syntax check after editing .py files
