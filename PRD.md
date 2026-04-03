# DryDock — Local CLI Coding Agent

**Repository:** https://github.com/fbobe321/drydock
**PyPI:** https://pypi.org/project/drydock-cli/ (v2.2.1)
**License:** Apache 2.0 (fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe))

## Vision

Best-in-class local coding agent. Build, debug, and ship software using local LLMs on consumer hardware.

## Current Status

- **Model:** devstral-24B-AWQ-4bit on 2x RTX 4060 Ti 16GB
- **SWE-bench Verified:** 42% file match (target: 68%)
- **Version:** 2.2.1
- **Users can:** Build projects from PRDs, fix bugs, review code, refactor

## Features

### Core
- 24 builtin tools (bash, grep, read_file, write_file, search_replace, etc.)
- 7 bundled skills (/investigate, /review, /ship, /batch, /simplify, /deep-research, /create-presentation)
- Multi-agent delegation (explore, diagnostic, planner subagents)
- Plan→Edit workflow (Mistral's designed pattern)
- AGENTS.md support (cross-tool standard, auto-created)
- Textual TUI with wave spinner, message queuing

### Local Model Support
- `/setup-model` command (vLLM, Ollama, LM Studio, custom)
- `--local` CLI flag for quick setup
- Auto-detect model name from server

### Safety
- Tool permission system (always/ask/never per tool)
- Loop guidance (never stops, only redirects)
- API error auto-recovery (retry after 10s)

## Install

```bash
pip install drydock-cli
drydock --local http://localhost:8000/v1
```

## Per-Project Instructions

DryDock loads instructions from these files in the project root:
- **AGENTS.md** — Cross-tool standard (recommended)
- **DRYDOCK.md** — DryDock-specific
- **.drydock/rules/*.md** — Modular rules

Auto-created if none exist. Essential for devstral to use subagents properly.

## Roadmap

### Near-term
- Reach 68% on SWE-bench Verified (from 42%)
- Static/dynamic prompt split for prefix caching
- Layered context compaction (4 fallback layers)
- Composable prompt sections (task-type specific)
- Background resource preloading

### Medium-term
- Real test execution in SWE-bench eval (not just file match)
- Repo-specific strategies (Django vs sympy vs sphinx)
- Draft-then-verify pattern for patches
- Windows Ctrl-C fix

### Long-term
- Support larger models as hardware improves
- Plugin marketplace for custom tools/skills
- Web dashboard for monitoring long runs
- Multi-model routing (different models for different tasks)

## Architecture Notes

See [CLAUDE.md](CLAUDE.md) for technical details, file locations, constraints, and development workflow.

## Legal

All code is original or forked from Apache 2.0 mistral-vibe. Architecture improvements are standard design patterns implemented from scratch. No proprietary code copied.
