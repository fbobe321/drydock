# DryDock

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/release/python-3120/)
[![License](https://img.shields.io/github/license/fbobe321/drydock)](https://github.com/fbobe321/drydock/blob/main/LICENSE)

```
     ____                 _             _    
    |  _ \ _ __ _   _  __| | ___   ___ | | __
    | | | | '__| | | |/ _` |/ _ \ / __|| |/ /
    | |_| | |  | |_| | (_| | (_) | (__ |   < 
    |____/|_|   \__, |\__,_|\___/ \___||_|\_\
                |___/
```

**Local-first CLI coding agent. Chart your course. Execute with precision.**

DryDock is a TUI coding assistant designed to work with **local LLMs**. It provides a conversational interface to your codebase — explore, modify, build, and test projects through natural language and a powerful set of tools.

> [!IMPORTANT]
> DryDock is tested and optimized for **Gemma 4 26B-A4B** (26B MoE, 4B active parameters) served via vLLM. Other models and providers are supported (Mistral, OpenAI, Anthropic, Ollama) but are not as thoroughly tested. If you use a different model, expect to tune prompts and tool settings.

## Tested Hardware + Model

| Component | Spec |
|-----------|------|
| GPUs | 2x NVIDIA RTX 4060 Ti 16GB |
| Model | [Gemma-4-26B-A4B-it-AWQ-4bit](https://huggingface.co/casperhansen/gemma-4-26b-a4b-it-AWQ-4bit) |
| Serving | vLLM (Docker image `vllm/vllm-openai:gemma4`) |
| Performance | ~70 tok/s, 131K context, 0% timeouts |
| Active params | 4B per token (MoE architecture — fast inference) |

### Install the model

```bash
# Download the model weights (requires HuggingFace access)
pip install huggingface-hub
huggingface-cli download casperhansen/gemma-4-26b-a4b-it-AWQ-4bit \
    --local-dir /path/to/models/Gemma-4-26B-A4B-it-AWQ-4bit
```

### Start vLLM

```bash
docker run -d \
    --gpus all \
    --name gemma4 \
    -p 8000:8000 \
    -v /path/to/models:/models \
    --ipc=host \
    vllm/vllm-openai:gemma4 \
    --model /models/Gemma-4-26B-A4B-it-AWQ-4bit \
    --quantization compressed-tensors \
    --tensor-parallel-size 2 \
    --max-model-len 131072 \
    --max-num-seqs 2 \
    --gpu-memory-utilization 0.95 \
    --kv-cache-dtype fp8 \
    --served-model-name gemma4 \
    --trust-remote-code \
    --tool-call-parser gemma4 \
    --enable-auto-tool-choice \
    --attention-backend TRITON_ATTN
```

Key flags:
- `--tensor-parallel-size 2` — split across 2 GPUs
- `--kv-cache-dtype fp8` — reduce KV cache memory for longer contexts
- `--tool-call-parser gemma4` + `--enable-auto-tool-choice` — required for Gemma 4 tool calling
- `--max-num-seqs 2` — limit concurrent requests (prevents OOM on 16GB GPUs)

Verify the model is running:
```bash
curl http://localhost:8000/v1/models
```

### Configure DryDock

```toml
# ~/.drydock/config.toml
[models.gemma4]
name = "gemma4"
provider = "generic-openai"
alias = "gemma4"
temperature = 0.2
thinking = "high"

[providers.generic-openai]
api_base = "http://localhost:8000/v1"
api_style = "openai"
```

## Install

```bash
pip install drydock-cli
```

Or with uv:
```bash
uv tool install drydock-cli
```

## Quick Start

```bash
cd your-project/
drydock
```

First run creates a config at `~/.drydock/config.toml` and prompts for your provider setup.

```
> Review the PRD and build the package
```

## Features

- **TUI Interface**: Full terminal UI with streaming output, tool approval, and session management.
- **Adaptive Thinking**: Automatically adjusts reasoning depth per turn — full thinking for planning, fast mode for file writes.
- **Powerful Toolset**: Read, write, and patch files. Execute shell commands. Search code with `grep`. Delegate to subagents.
- **Project-Aware**: Scans project structure, loads `AGENTS.md` / `DRYDOCK.md` for context.
- **Subagent Delegation**: Large tasks can be delegated to builder/planner/explorer subagents with isolated context.
- **Loop Detection**: Advisory-only detection that nudges the model away from repetitive actions without blocking.
- **Conda/Pip Support**: Auto-approves `pip install`, `conda install`, `pytest`, and other dev commands.
- **Bundled Skills**: Ships with skills like `create-presentation` for PowerPoint generation.
- **MCP Support**: Connect Model Context Protocol servers for extended capabilities.
- **Safety First**: Tool execution approval with `--dangerously-skip-permissions` for full auto-approve.

### Built-in Agents

- **`default`**: Standard agent that requires approval for tool executions.
- **`plan`**: Read-only agent for exploration and planning.
- **`accept-edits`**: Auto-approves file edits only.
- **`auto-approve`**: Auto-approves all tool executions.

```bash
drydock --agent plan
```

### Gemma 4 Optimizations

DryDock includes several optimizations specifically tuned for Gemma 4:

- **Simplified prompt** (`gemma4.md`): 20-line system prompt instead of 125 lines. Complex prompts cause Gemma 4 to plan instead of act.
- **Non-streaming mode**: Streaming breaks Gemma 4 tool call JSON parsing. DryDock automatically disables streaming for Gemma 4.
- **Thinking token filtering**: Gemma 4 leaks `<|channel>thought<channel|>` tokens into text output. DryDock strips these before storing in context.
- **Adaptive thinking**: Full thinking for planning (turn 1) and error recovery. Thinking OFF for routine file writes — eliminates 30-120s hangs between files.
- **search_replace resilience**: Auto-detects already-applied edits, infers missing file paths, fuzzy-matches whitespace differences.
- **Reduced tool set**: Disables tools that confuse Gemma 4 (`ask_user_question`, `task_create`, etc.).

## Usage

### Interactive Mode

```bash
drydock                        # Start interactive session
drydock "Fix the login bug"    # Start with a prompt
drydock --continue             # Resume last session
drydock --resume abc123        # Resume specific session
```

**Keyboard shortcuts:**
- `Ctrl+C` — Cancel current operation (double-tap to quit)
- `Shift+Tab` — Toggle auto-approve mode
- `Ctrl+O` — Toggle tool output
- `Ctrl+G` — Open external editor
- `@` — File path autocompletion
- `!command` — Run shell command directly

### Programmatic Mode

```bash
drydock --prompt "Analyze the codebase" --max-turns 5 --output json
drydock --dangerously-skip-permissions -p "Fix all lint errors"
```

## Configuration

DryDock is configured via `config.toml`. It looks first in `./.drydock/config.toml`, then `~/.drydock/config.toml`.

### API Key

```bash
drydock --setup                              # Interactive setup
export MISTRAL_API_KEY="your_key"            # Or set env var
```

Keys are saved to `~/.drydock/.env`.

### Consultant Model

Set a smarter model for the `/consult` command:

```toml
consultant_model = "gemini-2.5-pro"
```

The consultant provides read-only advice — it never calls tools. Use `/consult <question>` to ask it.

### Custom Agents

Create agent configs in `~/.drydock/agents/`:

```toml
# ~/.drydock/agents/redteam.toml
active_model = "devstral-2"
system_prompt_id = "redteam"
disabled_tools = ["search_replace", "write_file"]
```

### Skills

DryDock discovers skills from:
1. Custom paths in `config.toml` via `skill_paths`
2. Project `.drydock/skills/` or `.agents/skills/`
3. Global `~/.drydock/skills/`
4. Bundled skills (shipped with the package)

### MCP Servers

```toml
[[mcp_servers]]
name = "fetch_server"
transport = "stdio"
command = "uvx"
args = ["mcp-server-fetch"]
```

## Testing

DryDock uses a **shakedown harness** (`scripts/shakedown.py`) that drives the real TUI via pexpect and judges on user-perceptible criteria — not tool-call counts.

```bash
# Single project test
python3 scripts/shakedown.py \
    --cwd /path/to/project \
    --prompt "review the PRD and build the package" \
    --pkg package_name

# Interactive back-and-forth test
python3 scripts/shakedown_interactive.py \
    --cwd /path/to/project \
    --pkg package_name

# Full regression suite (370 PRDs)
bash scripts/shakedown_suite.sh
```

Pass criteria: no write loops, no ignored interrupts, no search_replace cascades, package executes, session finishes within time budget.

## Slash Commands

Type `/help` in the input for available commands. Create custom slash commands via the skills system.

## Session Management

```bash
drydock --continue              # Continue last session
drydock --resume abc123         # Resume specific session
drydock --workdir /path/to/dir  # Set working directory
```

## License

Copyright 2025 Mistral AI (original work)
Copyright 2026 DryDock contributors (modifications)

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

DryDock is a fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe) (Apache 2.0). See [NOTICE](NOTICE) for attribution.
