#!/usr/bin/env python3
"""Test the TUI code path without an interactive terminal.

This exercises the SAME code path as the TUI (streaming, render_path_prompt,
skill handling, approval callbacks) but captures output as text.

Usage:
    python3 scripts/test_tui_path.py -p "Read PRD.md and build the package" --cwd /path/to/project
    python3 scripts/test_tui_path.py -p "Fix the bug" --timeout 300
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# Use the installed drydock
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("MISTRAL_API_KEY", "dummy")

from drydock import __version__
from drydock.core.config.harness_files import init_harness_files_manager
init_harness_files_manager()

from drydock.cli.cli import get_initial_agent_name
from drydock.core.agent_loop import AgentLoop
from drydock.core.config import DrydockConfig, SessionLoggingConfig
from drydock.core.types import (
    AssistantEvent, ToolCallEvent, ToolResultEvent, UserMessageEvent,
)

# Simulate what TUI does: render_path_prompt
from drydock.core.autocompletion.path_prompt_adapter import render_path_prompt


async def run_tui_path(prompt: str, timeout: int = 300, cwd: str = "."):
    """Run the exact TUI code path but capture output."""
    os.chdir(cwd)

    config = DrydockConfig.load(
        session_logging=SessionLoggingConfig(enabled=False)
    )
    # Ensure we use the user's active model
    print(f"   Config active_model: {config.active_model}")
    print(f"   Models: {[m.alias or m.name for m in config.models]}")

    # TUI mode: streaming enabled, same tool filtering
    # This is what cli.py does for TUI mode
    try:
        active = config.get_active_model()
        if "gemma" in active.name.lower():
            config.disabled_tools = [
                *config.disabled_tools,
                "ask_user_question",
                "todo",
            ]
    except (ValueError, AttributeError):
        pass

    agent_loop = AgentLoop(
        config,
        agent_name="auto-approve",
        enable_streaming=True,  # TUI uses streaming
    )

    # TUI does render_path_prompt — this is a key difference from headless
    max_embed = 8 * 1024  # Gemma 4 limit
    rendered = render_path_prompt(prompt, base_dir=Path.cwd(), max_embed_bytes=max_embed)

    print(f"⚓ DryDock v{__version__} TUI-path test")
    print(f"   Model: {config.active_model}")
    print(f"   CWD: {os.getcwd()}")
    print(f"   Prompt: {prompt[:100]}...")
    print(f"   Rendered prompt size: {len(rendered)} chars")
    print(f"   Timeout: {timeout}s")
    print(f"{'='*60}")

    start = time.time()
    tool_calls = 0
    writes = {}  # Track writes per file
    errors = 0

    try:
        async def _run_with_timeout():
            async for event in agent_loop.act(rendered):
                yield event

        deadline = time.time() + timeout
        async for event in agent_loop.act(rendered):
            if time.time() > deadline:
                print(f"\n  ⏰ TIMEOUT after {timeout}s")
                break
            elapsed = int(time.time() - start)

            if isinstance(event, UserMessageEvent):
                pass  # Skip user message echo

            elif isinstance(event, AssistantEvent):
                if event.content:
                    # Print text output (truncated)
                    text = event.content.strip()
                    if text:
                        for line in text.split('\n')[:5]:
                            print(f"  💬 {line[:120]}")
                        if text.count('\n') > 5:
                            print(f"  💬 ... ({text.count(chr(10))} more lines)")

            elif isinstance(event, ToolCallEvent):
                tool_calls += 1
                name = event.tool_name or "?"
                args_preview = ""
                if hasattr(event, 'args') and event.args:
                    if hasattr(event.args, 'file_path'):
                        args_preview = event.args.file_path
                    elif hasattr(event.args, 'command'):
                        args_preview = event.args.command[:60]
                    elif hasattr(event.args, 'pattern'):
                        args_preview = event.args.pattern
                    elif hasattr(event.args, 'path'):
                        args_preview = event.args.path

                # Detect write loops
                if name == "write_file" and args_preview:
                    writes[args_preview] = writes.get(args_preview, 0) + 1
                    if writes[args_preview] > 2:
                        print(f"  ⚠️  LOOP DETECTED: {args_preview} written {writes[args_preview]} times!")

                print(f"  [{elapsed:3d}s] 🔧 {name}: {args_preview}")

            elif isinstance(event, ToolResultEvent):
                success = True
                msg = ""
                if hasattr(event, 'error') and event.error:
                    success = False
                    errors += 1
                    msg = str(event.error)[:100]
                elif hasattr(event, 'result'):
                    r = event.result
                    if hasattr(r, 'message'):
                        msg = r.message[:80] if r.message else ""
                    elif isinstance(r, str):
                        msg = r[:80]

                mark = "✓" if success else "✗"
                if msg:
                    print(f"         {mark} {msg}")

    except asyncio.TimeoutError:
        print(f"\n  ⏰ TIMEOUT")
    except Exception as e:
        print(f"\n  ❌ ERROR: {e}")

    elapsed = int(time.time() - start)
    print(f"\n{'='*60}")
    print(f"RESULTS:")
    print(f"  Duration: {elapsed}s")
    print(f"  Tool calls: {tool_calls}")
    print(f"  Errors: {errors}")
    print(f"  Files written: {dict(writes)}")

    # Check if --help works
    # Find package directories
    for d in Path.cwd().iterdir():
        if d.is_dir() and (d / "__init__.py").exists():
            import subprocess
            result = subprocess.run(
                [sys.executable, "-m", d.name, "--help"],
                capture_output=True, text=True, timeout=10,
                cwd=str(Path.cwd()),
            )
            if result.returncode == 0:
                print(f"  --help: ✓ PASS ({d.name})")
            else:
                print(f"  --help: ✗ FAIL ({d.name})")
                print(f"    {result.stderr[:200]}")

    # Detect issues
    issues = []
    for f, count in writes.items():
        if count > 2:
            issues.append(f"WRITE LOOP: {f} written {count} times")
    if errors > 5:
        issues.append(f"HIGH ERROR RATE: {errors} tool errors")
    if elapsed >= timeout:
        issues.append("TIMEOUT")

    if issues:
        print(f"\n⚠️  ISSUES DETECTED:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print(f"\n✅ No issues detected")

    return len(issues) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test DryDock TUI code path")
    parser.add_argument("-p", "--prompt", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    success = asyncio.run(run_tui_path(args.prompt, args.timeout, args.cwd))
    sys.exit(0 if success else 1)
