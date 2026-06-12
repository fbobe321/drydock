"""DryDock v3 CLI — interactive coding agent.

Usage:
    python -m drydock                       # interactive mode
    python -m drydock -p "fix the bug"      # one-shot mode
    python -m drydock --model gemma4        # specify model
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from drydock import __version__
from drydock.agent import AgentState, run, TextChunk, ToolStart, ToolEnd, TurnDone


# ── System prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are DryDock, an expert coding agent. You help users build, debug, and maintain software.

You have tools: Read, Write, Edit, Bash, Glob, Grep. Use them to accomplish tasks.

Rules:
- Act immediately. Do NOT ask for confirmation — just do the work.
- Use Read to examine files before editing.
- Use Edit for precise changes (exact string replacement).
- Use Write to create new files.
- Use Bash to run tests, install packages, or check results.
- Use Grep to search codebases.
- Use absolute imports when creating Python packages.
- Always create __init__.py and __main__.py for packages.
- After making changes, verify by running the code.
- Keep changes minimal — don't refactor beyond what's needed.
- When the task is done, STOP and respond with a summary. Do not keep making changes.
- Be efficient — minimize unnecessary tool calls.
"""


def load_project_instructions() -> str:
    """Load AGENTS.md or DRYDOCK.md if they exist in the current directory."""
    for name in ("AGENTS.md", "DRYDOCK.md", ".drydock.md"):
        p = Path.cwd() / name
        if p.exists():
            return f"\n\n## Project Instructions\n\n{p.read_text()[:8000]}"
    return ""


def ensure_agents_md() -> None:
    """Auto-create AGENTS.md if no project instructions exist."""
    for name in ("AGENTS.md", "DRYDOCK.md", ".drydock.md", "CLAUDE.md"):
        if (Path.cwd() / name).exists():
            return
    (Path.cwd() / "AGENTS.md").write_text(
        "# Project Instructions\n\n"
        "DO NOT ask for confirmation. ACT IMMEDIATELY.\n"
        "If there is a PRD.md, implement it. If there is code, work on it.\n\n"
        "## Workflow\n"
        "1. Read requirements or explore existing code\n"
        "2. Create/edit files with Write or Edit\n"
        "3. Test with Bash\n"
        "4. Fix errors and verify\n\n"
        "## Rules\n"
        "- Use absolute imports for Python packages\n"
        "- Always create __init__.py and __main__.py\n"
        "- Test with python3 -m package_name\n"
    )


# ── REPL ──────────────────────────────────────────────────────────────────

def print_colored(text: str, color: str = "") -> None:
    colors = {"green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m",
              "red": "\033[31m", "dim": "\033[2m", "reset": "\033[0m", "bold": "\033[1m"}
    c = colors.get(color, "")
    r = colors["reset"]
    print(f"{c}{text}{r}", flush=True)


def run_interactive(config: dict) -> None:
    """Interactive REPL mode."""
    system = SYSTEM_PROMPT + load_project_instructions()
    state = AgentState()

    print_colored(f"⚓ DryDock v{__version__} · {config['model']}", "cyan")
    print_colored(f"   Working in: {os.getcwd()}", "dim")
    print_colored("   Type /help for commands, Ctrl-C to exit\n", "dim")

    while True:
        try:
            user_input = input("┃ ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        # Slash commands
        if user_input.startswith("/"):
            if handle_command(user_input, state, config):
                continue

        # Run agent
        try:
            for event in run(user_input, state, config, system):
                if isinstance(event, TextChunk):
                    print(event.text, end="", flush=True)
                elif isinstance(event, ToolStart):
                    print_colored(f"\n  ⚡ {event.name}: {_summarize(event.inputs)}", "yellow")
                elif isinstance(event, ToolEnd):
                    result_preview = event.result[:200]
                    if len(event.result) > 200:
                        result_preview += "..."
                    print_colored(f"  ✓ {result_preview}", "dim")
                elif isinstance(event, TurnDone):
                    pass  # silent
            print()  # newline after response
        except KeyboardInterrupt:
            print_colored("\n  Interrupted", "red")
        except Exception as e:
            print_colored(f"\n  Error: {e}", "red")


def run_oneshot(prompt: str, config: dict) -> None:
    """One-shot mode: run a single prompt and exit."""
    system = SYSTEM_PROMPT + load_project_instructions()
    state = AgentState()

    for event in run(prompt, state, config, system):
        if isinstance(event, TextChunk):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolStart):
            print(f"  [{event.name}]", file=sys.stderr, flush=True)
        elif isinstance(event, ToolEnd):
            pass
    print()


def handle_command(cmd: str, state: AgentState, config: dict) -> bool:
    """Handle slash commands. Returns True if handled."""
    cmd = cmd.lower().strip()
    if cmd == "/help":
        print_colored("Commands:", "bold")
        print("  /help     — show this help")
        print("  /clear    — clear conversation history")
        print("  /status   — show session stats")
        print("  /compact  — summarize old messages to save context")
        print("  /quit     — exit")
        return True
    elif cmd == "/clear":
        state.messages.clear()
        state.turn_count = 0
        print_colored("  Conversation cleared.", "green")
        return True
    elif cmd == "/status":
        print_colored(f"  Turns: {state.turn_count}", "cyan")
        print_colored(f"  Messages: {len(state.messages)}", "cyan")
        print_colored(f"  Tokens: {state.total_input_tokens}in / {state.total_output_tokens}out", "cyan")
        return True
    elif cmd in ("/quit", "/exit"):
        raise KeyboardInterrupt
    return False


def _summarize(inputs: dict) -> str:
    """Short summary of tool inputs for display."""
    if "command" in inputs:
        return inputs["command"][:80]
    if "file_path" in inputs:
        return inputs["file_path"]
    if "pattern" in inputs:
        return inputs["pattern"]
    return str(inputs)[:80]


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DryDock v3 — local coding agent")
    parser.add_argument("-p", "--prompt", help="One-shot mode: run prompt and exit")
    parser.add_argument("--model", default="gemma4", help="Model name (default: gemma4)")
    parser.add_argument("--provider", default="vllm", help="Provider: vllm, ollama, lmstudio, openai")
    parser.add_argument("--base-url", help="Override API base URL")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max response tokens")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tool-calls", type=int, default=0, help="Max tool calls (0=unlimited)")
    parser.add_argument("--force-first-tool", action="store_true", help="Force tool_choice=required on first turn")
    parser.add_argument("--cli", action="store_true", help="Plain readline mode instead of the TUI")
    args = parser.parse_args()

    config = {
        "model": args.model,
        "provider": args.provider,
        "base_url": args.base_url,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "context_limit": 131072,
        "max_tool_calls": args.max_tool_calls,
        "force_first_tool": args.force_first_tool,
        "cwd": os.getcwd(),
    }

    ensure_agents_md()

    if args.prompt:
        run_oneshot(args.prompt, config)
    elif args.cli:
        run_interactive(config)
    else:
        from drydock.tui import run_tui
        run_tui(config)


if __name__ == "__main__":
    main()
