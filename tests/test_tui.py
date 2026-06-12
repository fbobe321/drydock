"""Tests for the TUI widgets and app mount/slash handling."""
from __future__ import annotations

import asyncio

from drydock.tui.app import DrydockApp
from drydock.tui.messages import AgentFinished
from drydock.tui.widgets import (
    PromptHistory,
    ToolCard,
    result_is_ok,
    summarize_inputs,
)


def test_prompt_history_up_down_recall():
    h = PromptHistory()
    h.add("first")
    h.add("second")
    # Up from an empty draft walks newest → oldest.
    assert h.up("") == "second"
    assert h.up("second") == "first"
    assert h.up("first") == "first"  # clamps at oldest
    # Down walks back toward the draft.
    assert h.down("first") == "second"
    assert h.down("second") == ""    # past newest → restored draft ("")


def test_prompt_history_preserves_draft():
    h = PromptHistory()
    h.add("ls")
    # User has typed "half-typed" then presses Up.
    assert h.up("half-typed") == "ls"
    # Down past the newest restores the in-progress draft.
    assert h.down("ls") == "half-typed"


def test_prompt_history_skips_consecutive_dupes_and_empty():
    h = PromptHistory()
    h.add("same")
    h.add("same")
    h.add("   ")  # blank ignored
    assert h.up("") == "same"
    assert h.up("same") == "same"  # only one entry stored


def test_prompt_history_down_noop_when_not_navigating():
    h = PromptHistory()
    h.add("x")
    # Pressing Down without having pressed Up leaves the line untouched.
    assert h.down("typing") == "typing"


def test_prompt_history_persists_across_sessions(tmp_path):
    path = tmp_path / "sub" / "history"  # parent dir does not exist yet
    h1 = PromptHistory(path)
    h1.add("build the thing")
    h1.add("run the tests")
    # A fresh instance pointed at the same file recalls prior entries.
    h2 = PromptHistory(path)
    assert h2.up("") == "run the tests"
    assert h2.up("run the tests") == "build the thing"


def test_prompt_history_caps_file_length(tmp_path):
    path = tmp_path / "history"
    h = PromptHistory(path, max_entries=3)
    for i in range(10):
        h.add(f"cmd {i}")
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert lines == ["cmd 7", "cmd 8", "cmd 9"]


def test_prompt_history_collapses_newlines_in_persisted_entry(tmp_path):
    path = tmp_path / "history"
    h = PromptHistory(path)
    h.add("line one\nline two")
    assert path.read_text().splitlines()[0] == "line one line two"


def test_prompt_history_survives_unreadable_file(tmp_path):
    path = tmp_path  # a directory, not a file → read/write raise OSError
    h = PromptHistory(path)
    h.add("noop")  # must not raise
    assert h.up("") == "noop"  # still works in memory


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


def test_busy_input_is_queued_and_drained():
    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            # Replace the agent worker with a recorder so nothing hits the LLM.
            started: list[str] = []
            app._run_agent = lambda text: started.append(text)  # type: ignore[method-assign]
            inp = app.query_one("#prompt")

            inp.value = "first task"
            await pilot.press("enter")
            await pilot.pause()
            assert started == ["first task"]
            assert app._busy and app._queue == []

            # Second submit while busy → queued, not started.
            inp.value = "second task"
            await pilot.press("enter")
            await pilot.pause()
            assert started == ["first task"]
            assert app._queue == ["second task"]
            assert "1 queued" in app._status_text()

            # When the first turn finishes, the queued one drains automatically.
            app.post_message(AgentFinished())
            await pilot.pause()
            assert started == ["first task", "second task"]
            assert app._queue == []

    asyncio.run(main())
