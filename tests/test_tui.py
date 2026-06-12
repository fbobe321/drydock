"""Tests for the TUI widgets and app mount/slash handling."""
from __future__ import annotations

import asyncio

from drydock.tui.app import DrydockApp
from drydock.tui.widgets import ToolCard, result_is_ok, summarize_inputs


def test_result_is_ok_marks_failures():
    assert result_is_ok("Wrote 11 lines")
    assert result_is_ok("apple: 3\nbanana: 2")
    assert not result_is_ok("Error: nope")
    assert not result_is_ok("REFUSED: this command reformats a filesystem...")
    assert not result_is_ok("  Error: leading whitespace still counts")


def test_summarize_inputs_prefers_meaningful_keys():
    assert summarize_inputs({"command": "ls -la"}) == "ls -la"
    assert summarize_inputs({"file_path": "a/b.py"}) == "a/b.py"
    assert summarize_inputs({"pattern": "foo"}) == "foo"
    long = "x" * 200
    assert summarize_inputs({"command": long}).endswith("…")


def test_toolcard_title_shows_name_not_none():
    c = ToolCard("Write", "fib.py")
    assert "Write" in c.title
    c.finish("Wrote 11 lines", ok=True)
    assert "Write" in c.title and "None" not in c.title
    assert c.has_class("ok")
    c2 = ToolCard("Bash", "boom")
    c2.finish("Error: nope", ok=False)
    assert c2.has_class("fail")


def test_app_mounts_and_handles_slash():
    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#banner")
            assert app.query_one("#status")
            inp = app.query_one("#prompt")
            inp.value = "/help"
            await pilot.press("enter")
            await pilot.pause()
            inp.value = "/clear"
            await pilot.press("enter")
            await pilot.pause()
            # empty submit is a no-op, not a crash
            inp.value = ""
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(main())
