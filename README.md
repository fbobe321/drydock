# DryDock

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/release/python-3120/)
[![License](https://img.shields.io/github/license/fbobe321/drydock)](https://github.com/fbobe321/drydock/blob/main/LICENSE)

```
     ____             ____             _    
    |  _ \ _ __ _   _|  _ \  ___   ___| | __
    | | | | '__| | | | | | |/ _ \ / __| |/ /
    | |_| | |  | |_| | |_| | (_) | (__|   < 
    |____/|_|   \__, |____/ \___/ \___|_|\_\
                |___/                       
```

**Local-first CLI coding agent. Chart your course. Execute with precision.**

DryDock is a TUI coding assistant designed to work with **local LLMs**. It provides a conversational interface to your codebase — explore, modify, build, and test projects through natural language and a powerful set of tools.

> [!IMPORTANT]
> DryDock is tested and optimized for **Gemma 4 26B-A4B** (26B MoE, 4B active parameters). **Recommended serving stack: llama.cpp** with `--jinja` (the chat-template fix that prevents the tool-call loops Gemma 4 hits under other backends). vLLM is also documented below as a higher-throughput alternative for batch/eval workloads. Other models and providers are supported (Mistral, OpenAI, Anthropic, Ollama) but are not as thoroughly tested. If you use a different model, expect to tune prompts and tool settings.

## Tested Hardware + Model

| Component | Spec |
|-----------|------|
| GPUs | 2× NVIDIA RTX 4060 Ti 16GB |
| Model (llama.cpp, recommended) | [unsloth/gemma-4-26B-A4B-it-GGUF](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF) — UD-Q3_K_M (12.7GB) or UD-Q4_K_M (16.9GB) |
| Model (vLLM, alternative) | [casperhansen/gemma-4-26b-a4b-it-AWQ-4bit](https://huggingface.co/casperhansen/gemma-4-26b-a4b-it-AWQ-4bit) |
| Performance | ~15–17 tok/s decode (llama.cpp Q3), ~70 tok/s decode (vLLM AWQ) |
| Active params | 4B per token (MoE architecture — fast inference) |

### Recommended path: llama.cpp + Unsloth GGUF

**Why this is the recommended setup:** Gemma 4's tool-calling format
requires precise chat-template handling. Without `--jinja`, `tool` results
get injected without the right turn markers and the model loops or
returns empty assistant messages — the exact 400 Bad Request loop
fixed in v2.7.39 (GH #14). With `--jinja`, the GGUF's bundled chat
template handles tool turns natively and the loops disappear.

```bash
# 1. Download Unsloth's GGUF (Q3_K_M is the article-recommended quant;
#    UD-Q4_K_M is a higher-quality alternative if you have ~17GB VRAM)
huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
    --include "gemma-4-26B-A4B-it-UD-Q3_K_M.gguf" \
    --local-dir /path/to/models

# 2. Build llama.cpp with CUDA (or use the Docker image
#    ghcr.io/ggml-org/llama.cpp:server-cuda)
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=OFF
cmake --build build --config Release -j8 --target llama-server

# 3. Start the server with the article recipe
./build/bin/llama-server \
    -m /path/to/models/gemma-4-26B-A4B-it-UD-Q3_K_M.gguf \
    --host 0.0.0.0 --port 8000 \
    -ngl 99 -c 32768 -np 1 \
    --jinja \
    -ctk q8_0 -ctv q8_0 \
    --alias gemma4
```

**Critical flags:**
- `--jinja` — **the loop-fix.** Required for tool-using workflows. Without it, Gemma 4 enters infinite retry loops on multi-turn tool sessions.
- `-ngl 99` — offload all layers to GPU
- `-c 32768` — 32K context (fits in 16GB VRAM with q8 KV cache)
- `-ctk q8_0 -ctv q8_0` — quantize KV cache for longer contexts
- `-np 1` — single slot (concurrent requests serialize)
- `--alias gemma4` — what the API reports as the `model` field

**Drydock config** (`~/.drydock/config.toml`):

```toml
active_model = "gemma4"

[[providers]]
name = "local"
api_base = "http://localhost:8000/v1"
api_key_env_var = ""
backend = "generic"

[[models]]
name = "gemma4"
provider = "local"
alias = "gemma4"
temperature = 1.0           # MUST be 1.0 with --jinja — lower temps reinforce loops
context_window = 32768       # Match `-c 32768` from llama-server. Drydock
                             # auto-clamps auto_compact_threshold to
                             # context_window − 4096 so we never blow past
                             # the server's max input.
auto_compact_threshold = 28000

# Article-recommended sampling (passed through extra_sampling to llama-server).
# Drydock auto-bakes these on first launch when llama.cpp is detected at
# 127.0.0.1:8080 / :8000, but you can override here.
[models.extra_params]
top_k = 40
top_p = 0.95
frequency_penalty = 1.1
max_tokens = 2048
```

### Alternative: vLLM (higher throughput, no `--jinja` equivalent)

vLLM has its own `--tool-call-parser gemma4` path that works for most
workflows, but has been observed to enter tool-call loops on long
multi-turn sessions (GH #14, fixed at the drydock side in v2.7.39 by
filtering empty assistant messages before re-call). Use vLLM when you
need higher decode throughput (~70 tok/s vs llama.cpp's ~15–17) for
batch eval or non-interactive workloads where loop-fix matters less.

```bash
huggingface-cli download casperhansen/gemma-4-26b-a4b-it-AWQ-4bit \
    --local-dir /path/to/models/Gemma-4-26B-A4B-it-AWQ-4bit

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
- `--tool-call-parser gemma4` + `--enable-auto-tool-choice` — required for Gemma 4 tool calling under vLLM
- `--max-num-seqs 2` — limit concurrent requests (prevents OOM on 16GB GPUs)

Verify either backend is running:
```bash
curl http://localhost:8000/v1/models
```

For vLLM, drydock config is the same as the llama.cpp block above,
except `temperature = 0.2` is fine — the `--jinja` requirement only
applies to llama.cpp.

## Install

```bash
pip install drydock-cli
```

Or with uv:
```bash
uv tool install drydock-cli
```

> [!TIP]
> **New install hitting weird behavior?** See [DEPLOYMENT.md](DEPLOYMENT.md)
> for the exact known-working vLLM launch flags, `~/.drydock/config.toml`,
> env vars, and a diagnostic checklist. Most "DryDock doesn't work" issues
> trace back to missing vLLM flags (`--tool-call-parser gemma4`,
> `--enable-auto-tool-choice`) or temperature/thinking config drift.

### Windows: `drydock` not found after install

Pip on Windows often warns:

```
WARNING: The scripts drydock.exe and drydock-acp.exe are installed in
'C:\Users\<you>\AppData\Roaming\Python\Python3xx\Scripts' which is not on PATH.
```

This is a generic `pip install --user` warning, not a drydock bug — Windows
doesn't add the per-user scripts directory to `PATH` by default. Three
workarounds, in increasing convenience:

**Option A — invoke without the shim (always works):**
```powershell
python -m drydock
```

**Option B — install in a venv (recommended):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install drydock-cli
drydock
```

**Option C — add the user-scripts directory to PATH once.** Drydock ships
a one-shot helper:
```powershell
python -m drydock --fix-windows-path
```
This appends `%APPDATA%\Python\Python3xx\Scripts` to your **user** `PATH`
environment variable (no admin required, no system PATH touched). Open a
fresh PowerShell session and `drydock` will resolve. To do it manually:
**System Properties → Environment Variables → User variables → Path → Edit
→ New →** paste the directory the warning printed.

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
- **MCP Support**: Connect Model Context Protocol servers via the `/mcp` slash command or `~/.drydock/config.toml`. See [MCP Servers](#mcp-servers).
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

DryDock can connect to any Model Context Protocol server. Tools from a server `foo` show up as `foo__<tool_name>` in the agent's tool list. Two ways to add one:

#### Option A — `/mcp` slash command (recommended)

Inside the TUI:

```
/mcp                                                       # list configured servers + usage
/mcp examples                                              # ready-to-paste examples
/mcp add stdio fetch_server uvx mcp-server-fetch
/mcp add stdio filesystem npx -y @modelcontextprotocol/server-filesystem /data
/mcp add http weather https://mcp.example.com
/mcp add streamable-http myapi https://api.example.com/mcp
/mcp remove fetch_server
```

The command writes to `~/.drydock/config.toml`. Restart DryDock to load new servers.

#### Option B — edit `~/.drydock/config.toml` directly

Use this for advanced fields (auth headers, env-var API keys, custom timeouts, `sampling_enabled`). Three transports: `stdio`, `http`, `streamable-http`.

```toml
[[mcp_servers]]
name = "fetch_server"
transport = "stdio"
command = "uvx"
args = ["mcp-server-fetch"]

[[mcp_servers]]
name = "filesystem"
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
env = { LOG_LEVEL = "info" }
startup_timeout_sec = 10.0
tool_timeout_sec = 60.0
sampling_enabled = true

[[mcp_servers]]
name = "github"
transport = "streamable-http"
url = "https://api.githubcopilot.com/mcp"
api_key_env = "GITHUB_TOKEN"                # reads token from this env var
api_key_header = "Authorization"
api_key_format = "Bearer {token}"

[[mcp_servers]]
name = "weather"
transport = "http"
url = "https://mcp.weather.example.com"
headers = { "X-Tenant" = "drydock" }
```

Verify a server loaded by running `/help` inside the TUI and checking for `<name>__*` prefixed tools. Server errors show up in `~/.drydock/logs/`.

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
