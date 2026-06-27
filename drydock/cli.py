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
    """Load AGENTS.md or DRYDOCK.md if they exist in the current directory.

    Framed as BACKGROUND context, not standing orders: a leftover aggressive
    AGENTS.md ("implement the PRD, create all files NOW") must not turn a plain
    "hello" into an autonomous build. The user's current message always wins.
    """
    for name in ("AGENTS.md", "DRYDOCK.md", ".drydock.md"):
        p = Path.cwd() / name
        if p.exists():
            return (
                "\n\n## Project conventions (background context)\n\n"
                "The notes below describe this project's conventions. Apply them "
                "ONLY when the user asks you to do work here. They are NOT a "
                "command to act now — your response is driven by the user's "
                "latest message, not by these notes.\n\n"
                f"{p.read_text()[:8000]}"
            )
    return ""


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
    from drydock.providers import LLMUnreachable

    system = SYSTEM_PROMPT + load_project_instructions()
    state = AgentState()

    try:
        for event in run(prompt, state, config, system):
            if isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolStart):
                # Include an input summary so the trace (captured to drydock.log
                # under -p) shows WHAT each tool did, not just its name. Without
                # this, a timed-out run is an opaque wall of "[Bash]" with no way
                # to tell a genuine 100-command exploration from a tight loop.
                print(f"  [{event.name}] {_summarize(event.inputs)}",
                      file=sys.stderr, flush=True)
            elif isinstance(event, ToolEnd):
                # One-line outcome so failures/repeats are visible in the trace.
                preview = " ".join(str(event.result).split())[:100]
                print(f"     -> {preview}", file=sys.stderr, flush=True)
        print()
    except LLMUnreachable as e:
        # The model server is down, the URL is wrong, or a turn overran the read
        # timeout. The TUI and --cli paths already surface this; one-shot mode
        # (-p, used by automation/harnesses) had no handler, so it crashed with a
        # raw traceback that buried the actionable message. Print the remediation
        # cleanly to stderr and exit non-zero so callers can detect the failure.
        print(f"\nError: {e}", file=sys.stderr, flush=True)
        sys.exit(2)


def handle_command(cmd: str, state: AgentState, config: dict) -> bool:
    """Handle slash commands. Returns True if handled."""
    raw = cmd.strip()  # case-preserved (paths/args must not be lowercased)
    cmd = raw.lower()
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
    elif cmd == "/compact":
        from drydock.compaction import compact, emergency_compact, estimate_tokens

        if not state.messages:
            print_colored("  Nothing to compact.", "dim")
            return True
        before = estimate_tokens(state.messages)
        limit = config.get("context_limit", 65536) or 65536
        state.messages = compact(state.messages, limit)
        if estimate_tokens(state.messages) > limit * 0.5:
            state.messages = emergency_compact(state.messages, limit)
        after = estimate_tokens(state.messages)
        saved = before - after
        if saved > 0:
            print_colored(
                f"  Compacted: ~{before:,} → ~{after:,} tokens (freed ~{saved:,}).",
                "green",
            )
        else:
            print_colored(f"  Already compact (~{before:,} tokens).", "dim")
        return True
    elif cmd == "/status":
        print_colored(f"  Turns: {state.turn_count}", "cyan")
        print_colored(f"  Messages: {len(state.messages)}", "cyan")
        print_colored(f"  Tokens: {state.total_input_tokens}in / {state.total_output_tokens}out", "cyan")
        return True
    elif cmd.split()[0] == "/graphrag":
        from drydock import graphrag

        cwd = config.get("cwd") or os.getcwd()
        store = graphrag.default_store_path(cwd)
        parts = raw.split()  # raw = case-preserved, so paths survive
        sub = parts[1].lower() if len(parts) > 1 else ""
        rest = " ".join(parts[2:]).strip()
        if sub == "build" and rest:
            print_colored(f"  Building knowledge base from {rest} …", "dim")
            try:
                stats = graphrag.build_index([rest], store, cwd=cwd)
                print_colored(
                    f"  ✓ {stats['files']} files · {stats['chunks']} chunks · "
                    f"{stats['entities']} entities · {stats['edges']} edges → {store}",
                    "green",
                )
            except Exception as e:  # noqa: BLE001
                print_colored(f"  graphrag build failed: {e}", "red")
        elif sub == "clear":
            store.unlink(missing_ok=True)
            print_colored("  Knowledge base cleared.", "green")
        else:
            index = graphrag.load_index(store)
            if index is None:
                print_colored("  No knowledge base. Build: /graphrag build <path>", "dim")
            else:
                print_colored(
                    f"  Knowledge base: {len(index.get('chunks', []))} chunks · "
                    f"{len(index.get('entities', {}))} entities ({store})", "cyan")
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

def _first_run_setup(cfg: dict, cfg_path, cfgmod) -> tuple[dict, str]:
    """Interactive first-launch: ask for the model server URL + model name and
    persist them to config.toml. Autodetect (if anything is listening) supplies
    the suggested defaults. Returns (updated cfg, onboarding line)."""
    from drydock import detect

    found = detect.detect_local_llms()
    default_url = found[0]["base_url"] if found else "http://127.0.0.1:8000/v1"
    default_model = (
        found[0]["models"][0]
        if found and found[0].get("models")
        else (cfg.get("model") or "gemma4")
    )
    print("\n  ⚓ Drydock — first-time setup")
    print("  Point Drydock at your local model server (any OpenAI-compatible URL).\n")
    try:
        url = input(f"  Model server URL [{default_url}]: ").strip() or default_url
        model = input(f"  Model name [{default_model}]: ").strip() or default_model
    except (EOFError, KeyboardInterrupt):
        url, model = default_url, default_model
        print()
    cfg["base_url"] = url
    cfg["model"] = model
    cfg.setdefault("provider", found[0]["provider"] if found else "vllm")
    cfgmod.save_file(cfg, cfg_path)
    print(f"\n  Saved to {cfg_path} — change it anytime with /model or by editing that file.\n")
    return cfg, f"⚓ Using {model} at {url}"


def main():
    parser = argparse.ArgumentParser(description="DryDock — local coding agent")
    parser.add_argument(
        "--version", "-V", action="version", version=f"drydock {__version__}"
    )
    # Config-backed flags default to None so an unset flag falls through to the
    # config file (see drydock.config). Runtime-only flags keep real defaults.
    parser.add_argument("-p", "--prompt", help="One-shot mode: run prompt and exit")
    parser.add_argument("--model", help="Model name (default: gemma4)")
    parser.add_argument("--provider", help="Provider: vllm, ollama, lmstudio, openai")
    parser.add_argument("--base-url", dest="base_url", help="Override API base URL")
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, help="Max response tokens")
    parser.add_argument(
        "--context-limit", dest="context_limit", type=int,
        help="Model server context window (must match the server's -c / "
             "--max-model-len; drives the ctx gauge + when compaction fires)",
    )
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-tool-calls", type=int, default=0, help="Max tool calls (0=unlimited)")
    parser.add_argument("--force-first-tool", action="store_true", help="Force tool_choice=required on first turn")
    parser.add_argument("--cli", action="store_true", help="Plain readline mode instead of the TUI")
    parser.add_argument(
        "--dangerously-skip-permissions",
        dest="dangerously_skip_permissions",
        action="store_true",
        help="Auto-approve all tool calls without prompting (for CI/cron use)",
    )
    args = parser.parse_args()

    from drydock import config as cfgmod

    cfg_path = cfgmod.default_config_path()
    first_run = not cfg_path.exists()
    cfg = cfgmod.resolve({
        "model": args.model,
        "provider": args.provider,
        "base_url": args.base_url,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "context_limit": args.context_limit,
    }, cfg_path)

    # First launch: ask the user for their model server URL + model name and
    # persist them to config.toml. Interactive only — when stdin isn't a TTY
    # (piped / -p / cron) we fall back to silent autodetect so nothing blocks.
    onboarding = ""
    if first_run and not args.provider and not args.base_url:
        if sys.stdin.isatty() and not args.prompt:
            cfg, onboarding = _first_run_setup(cfg, cfg_path, cfgmod)
        else:
            from drydock import detect

            found = detect.detect_local_llms()
            onboarding = detect.onboarding_message(found)
            if found:
                cfg["provider"] = found[0]["provider"]
                cfg["base_url"] = found[0]["base_url"]
                cfgmod.save_file(cfg, cfg_path)

    config = {
        # context_limit now comes from cfg (DEFAULTS < config.toml < --context-limit)
        # — it drives the ctx gauge AND when compaction fires, so it must match the
        # server's real -c. Previously hardcoded to 65536 here, which clobbered the
        # config value and overflowed servers running a smaller window.
        **cfg,
        "max_tool_calls": args.max_tool_calls,
        "force_first_tool": args.force_first_tool,
        "_approve_all": args.dangerously_skip_permissions,
        "cwd": os.getcwd(),
        "history_path": str(Path.home() / ".drydock" / "history"),
        "onboarding": onboarding,
    }

    # Load a user's OWN AGENTS.md/DRYDOCK.md as background context if present.
    # Drydock never CREATES one: auto-writing an AGENTS.md littered every
    # directory the user ran drydock in, and its "ACT IMMEDIATELY, implement
    # the PRD" content fought the system prompt and risked turning a plain
    # greeting into a runaway build. The TUI reads this from config; CLI modes
    # call load_project_instructions directly (see run_interactive/run_oneshot).
    config["project_instructions"] = load_project_instructions()

    if args.prompt:
        run_oneshot(args.prompt, config)
    elif args.cli:
        run_interactive(config)
    else:
        from drydock.tui import run_tui
        run_tui(config)


if __name__ == "__main__":
    main()
