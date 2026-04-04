# What's new in v2.2.6

- **Gemma 4 support**: Tool calling with Gemma 4 26B MoE via vLLM Docker — 3-4x faster inference
- **search_replace compatibility**: Accepts JSON, separator, and block formats for any model
- **Planner/diagnostic subagents**: Fixed missing prompt registration that crashed subagent delegation
- **Context-limit recovery**: Auto-truncates old tool results on 400 errors instead of looping
- **SWE-bench 70%**: File match rate up from 60% (devstral) to 70% (Gemma 4), 8x faster
