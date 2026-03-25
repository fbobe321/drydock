# DryDock

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/release/python-3120/)
[![License](https://img.shields.io/github/license/fbobe321/drydock)](https://github.com/fbobe321/drydock/blob/main/LICENSE)

```
         |    |    |
        )_)  )_)  )_)
       )___))___))___)\
      )____)____)_____)\\
    _____|____|____|____\\\__
---\                   /----
    \_________________/
  ~~~~ ~~~ ~~~~ ~~~ ~~~~
   ~~~ ~~~~ ~~~ ~~~ ~~~
```

**Nautical CLI coding agent. Chart your course. Execute with precision.**

DryDock is a command-line coding assistant that works with any LLM provider. It provides a conversational interface to your codebase, allowing you to use natural language to explore, modify, and interact with your projects through a powerful set of tools.

> [!WARNING]
> DryDock works on Windows, but we officially support and target UNIX environments.

### Install

```bash
pip install drydock-cli
```

Or with uv:

```bash
uv tool install drydock-cli
```

## Features

- **Interactive Chat**: A conversational AI agent that understands your requests and breaks down complex tasks.
- **Powerful Toolset**: Read, write, and patch files. Execute shell commands. Search code with `grep`. Manage todos. Delegate to subagents.
- **Project-Aware Context**: DryDock automatically scans your project's file structure and Git status.
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

## Quick Start

1. Navigate to your project directory and run:

   ```bash
   drydock
   ```

2. First run creates a config at `~/.drydock/config.toml` and prompts for your API key.

3. Start chatting:

   ```
   > Can you find all TODO comments in this project?
   ```

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

### Trust Folder System

DryDock includes a trust folder system. When you run DryDock in a directory with a `.drydock` folder, it asks you to confirm trust. Managed via `~/.drydock/trusted_folders.toml`.

## Configuration

DryDock is configured via `config.toml`. It looks first in `./.drydock/config.toml`, then `~/.drydock/config.toml`.

### API Key

```bash
drydock --setup                              # Interactive setup
export MISTRAL_API_KEY="your_key"            # Or set env var
```

Keys are saved to `~/.drydock/.env`.

### Custom Agents

Create agent configs in `~/.drydock/agents/`:

```toml
# ~/.drydock/agents/redteam.toml
active_model = "devstral-2"
system_prompt_id = "redteam"
disabled_tools = ["search_replace", "write_file"]
```

```bash
drydock --agent redteam
```

### Custom Prompts

Create markdown files in `~/.drydock/prompts/`:

```toml
system_prompt_id = "my_custom_prompt"
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

### Custom DryDock Home

```bash
export DRYDOCK_HOME="/path/to/custom/home"
```

This affects where DryDock looks for `config.toml`, `.env`, `agents/`, `prompts/`, `skills/`, and `logs/`.

## Slash Commands

Type `/help` in the input for available commands. Create custom slash commands via the skills system.

## Session Management

```bash
drydock --continue              # Continue last session
drydock --resume abc123         # Resume specific session
drydock --workdir /path/to/dir  # Set working directory
```

## Resources

- [CHANGELOG](CHANGELOG.md)
- [CONTRIBUTING](CONTRIBUTING.md)
- [ACP Setup](docs/acp-setup.md) — Editor/IDE integration

## License

Copyright 2025 Mistral AI (original work)
Copyright 2026 DryDock contributors (modifications)

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

DryDock is a fork of [mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe) (Apache 2.0). See [NOTICE](NOTICE) for attribution.
