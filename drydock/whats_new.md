# What's new in v2.7.51

- **Windows installs work**: dropped `python-dotenv` dependency that caused pip install failures, and tolerated Textual 8.2.5 API changes (theme registration, fence highlighter signature) that crashed `drydock` on first launch.
- **GraphRAG authoritative-answer recognition**: when the auto-prefetch hook surfaces a curated chunk with an `ANSWER:` marker, drydock steers the model to use it verbatim instead of re-deriving. 20-question HLE seeded-retrieval ablation lifted 5/20 → 18/20 (+45 pts).
- **TUI rendering**: tabs in tool output (git status, table-style results) no longer collide with markdown markers and produce jumbled output — expanded to 4-space stops outside fenced code blocks.
- **Config upgrade migration**: existing user configs now backfill missing top-level keys (`slim_system_prompt`, etc.) on every load, preserving user values. Fixes the "missing items in config.toml" bug for upgraders.
- **LLM connection failures fail fast**: `httpx` connect timeout dropped from 720s to 10s; unreachable LLM surfaces a clear actionable error ("Cannot reach LLM at X — verify server, check api_base, confirm network") in seconds instead of a 12-minute silent spinner.
- **Hallucinated tool suppression**: `lsp`, `read_mcp_resource`, `ralph_repo_index`, and friends now redirect to `glob`/`grep` with a concrete example — closes the empty-after-tool stall loop on Gemma 4.
- **First-launch autodetect prompts you to override**: detected Ollama/llama.cpp/vLLM/LM Studio at 127.0.0.1, the message now points at `drydock --setup` and the config path so you can change it without hunting docs.
