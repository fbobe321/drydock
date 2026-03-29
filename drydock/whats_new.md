# What's new in v1.6.0

- **Multi-phase build orchestrator**: Complex projects are built in phases (plan, scaffold, implement) with separate contexts per file — no more import loops or wasted turns
- **Auto-fix packaging**: `__main__.py` and absolute imports are handled automatically
- **Smarter circuit breaker**: Resets after code edits so retries work after fixing bugs
- **7 bundled skills**: /investigate, /review, /ship, /batch, /simplify, /deep-research, /create-presentation
- **65 PRD-driven tests**: Real-world project building verified against live backend
