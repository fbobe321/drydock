# What's new in v1.8.0

- **Multi-phase build orchestrator**: PRDs are built in phases — plan, scaffold, implement per file, auto-fix imports. 83% pass rate on 6 project types
- **Deterministic planning**: No LLM needed for project planning — PRD parsed directly
- **Auto-fix**: `__main__.py`, absolute imports, circular imports, cross-file name matching
- **7 bundled skills**: /investigate, /review, /ship, /batch, /simplify, /deep-research, /create-presentation
- **Smart circuit breaker**: Only blocks failed commands, allows retries after fixes
- **Vibe references removed**: All user-facing strings now say Drydock
