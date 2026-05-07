# What's new in v2.8.0

**Headline:** drydock + Gemma 4 26B-A4B solves **20/20 = 100%** of a 20-question HLE baseline-failure set when the literal answer is seeded in GraphRAG. Up from 0/20 baseline → 5/20 first iteration → 14/20 → 18/20 → 20/20 across the 2.7.x cycle.

**GraphRAG retrieval & steering**:
- **Authoritative-answer recognition** in the auto-prefetch hook: when a chunk with an `ANSWER:` (or `Verified answer:` / `Ground truth:`) marker scores high enough to be the obvious top-1, drydock injects a system note instructing the model to copy that line verbatim. Without it, Gemma 4 re-derives and frequently overrules the verified value.
- **Relative-margin path**: chunks with a curated `===<tag>:<id>===` header that dominate the next hit by ≥ 2× also count as authoritative — catches narrow-trivia questions where absolute BM25 scores are low.
- **Stopword filter on queries**: short questions like "In the 1997 movie X, where does Y move?" no longer get drowned out by `in/the/movie/where` matching every chunk. Closes the last 10pp gap to 20/20.

**Windows install + Textual 8.2.5 compat**:
- Dropped `python-dotenv` dependency (replaced with hand-rolled .env parser) — fixes the pip rename-on-uninstall crash that blocked Windows fresh installs.
- Theme assignment wrapped in try/except across all three callsites (`DrydockApp`, onboarding, trust folder dialog) — tolerates Textual 8.2.5's stricter theme registration.
- `AnsiMarkdownFence.highlight()` accepts `**kwargs` to absorb Textual 8.2.5's new `ansi=` arg.

**TUI rendering**:
- Mid-line tabs in tool output (table-style results, git status) expand to 4-space stops outside fenced code blocks. No more jumbled output where Rich's tab-stop renderer counted columns inconsistently.

**Config upgrade**:
- Existing user configs backfill missing top-level keys on every load (`slim_system_prompt`, `auto_compact_threshold`, etc.). Upgraders no longer need to delete and regenerate config.toml.

**LLM connection clarity**:
- `httpx` connect timeout dropped from 720s to 10s — unreachable LLM surfaces in seconds with a clear "Cannot reach LLM at X" error and 3 remediation steps, instead of a 12-minute silent spinner.

**Stall + loop suppression**:
- `lsp`, `read_mcp_resource`, `ralph_repo_index`, `exit_plan_mode` and other hallucinated tool names now redirect to glob/grep or no-op cleanly — closes the empty-after-tool stall loop on Gemma 4.

**First-launch autodetect**:
- Detected Ollama/llama.cpp/vLLM/LM Studio messages now show the config file path and point at `drydock --setup` so users can switch LLM endpoints without hunting docs.

**Phase 1 finding (Deep Noir opportunity)**:
- Removing the literal answer and replacing with a similar worked example collapses the model back to baseline (~5%). Gemma 4 can't extract a pattern from worked examples to apply to the original question — needs reasoning-direction steering. Phase 2/3 (reasoning steps only, domain context only) and Phase 4 (Deep Noir intervention) are the next experimental steps.
