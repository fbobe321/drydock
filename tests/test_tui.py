"""Tests for the TUI widgets and app mount/slash handling."""
from __future__ import annotations

import asyncio

from drydock.tui.app import DrydockApp
from drydock.tui.messages import AgentFinished
from drydock.tui.widgets import (
    PromptHistory,
    ToolCard,
    flatten_pasted_text,
    format_tool_body,
    result_is_ok,
    summarize_inputs,
)


def test_format_tool_body_expands_tabs():
    out = format_tool_body("M\tfile.py\nA\tnew.py")
    assert "\t" not in out
    assert "M   file.py" in out  # tab → 4-space stop


def test_format_tool_body_caps_long_output():
    out = format_tool_body("x" * 20000)
    assert "truncated for display" in out
    assert len(out) < 9000


def test_format_tool_body_empty():
    assert format_tool_body("   ") == "(no output)"
    assert format_tool_body("") == "(no output)"


def test_flatten_pasted_text_keeps_all_lines():
    pasted = "Traceback (most recent call last):\n  File 'x.py', line 3\nValueError: boom"
    flat = flatten_pasted_text(pasted)
    assert "\n" not in flat
    assert "Traceback" in flat and "ValueError: boom" in flat  # nothing dropped
    assert flat == "Traceback (most recent call last): File 'x.py', line 3 ValueError: boom"


def test_flatten_pasted_text_drops_blank_lines():
    assert flatten_pasted_text("a\n\n\nb") == "a b"
    assert flatten_pasted_text("single line") == "single line"


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
    # A failed shell command (Bash appends "[exit code: N]" only on failure)
    # must render ✗, not a green ✓ — otherwise the TUI hides real failures.
    assert not result_is_ok("bash: sqlite3: command not found\n[exit code: 127]")
    assert not result_is_ok("test.c:1: error\n[exit code: 1]")
    # Successful command output (no exit-code marker) stays ✓, even if it
    # happens to mention the word 'error' in normal output.
    assert result_is_ok("0 errors, 0 warnings\nBuild succeeded")


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
            inp.text = "/help"
            await pilot.press("enter")
            await pilot.pause()
            inp.text = "/clear"
            await pilot.press("enter")
            await pilot.pause()
            # empty submit is a no-op, not a crash
            inp.text = ""
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(main())


def test_tui_includes_project_instructions_in_system_prompt():
    cfg = {
        "model": "gemma4", "provider": "vllm", "cwd": "/tmp",
        "project_instructions": "\n\n## Project Instructions\n\nUse tabs not spaces.",
    }
    app = DrydockApp(cfg)
    assert "Use tabs not spaces." in app.system
    # A model switch rebuilds via the same path, so instructions are kept.
    assert "Use tabs not spaces." in app._build_system("mistral")


def test_transcript_renders_bracket_text_without_markup_error():
    # A tool result / model output with unbalanced brackets would raise a
    # Textual MarkupError if markup were enabled on the transcript widgets.
    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            from drydock.tui.widgets import AssistantMessage, ToolCard

            msg = AssistantMessage()
            app.query_one("#transcript").mount(msg)
            msg.append("here is some code: list[int] and a tag [not_closed")
            card = ToolCard("Grep", "x")
            app.query_one("#transcript").mount(card)
            await pilot.pause()
            card.finish("match at [line 3] foo[bar baz]", ok=True)
            await pilot.pause()  # forces a render; must not raise

    asyncio.run(main())


def test_slash_commands_model_cwd_status_undo(tmp_path):
    async def main():
        cfg = {"model": "gemma4", "provider": "vllm", "cwd": str(tmp_path)}
        app = DrydockApp(cfg)
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#prompt")

            async def slash(value):
                inp.text = value
                await pilot.press("enter")
                await pilot.pause()

            # /model with no arg shows current; with arg switches.
            await slash("/model")
            await slash("/model mistral")
            assert app.config["model"] == "mistral"

            # /cwd to a real dir switches; bad dir is rejected.
            sub = tmp_path / "work"
            sub.mkdir()
            await slash(f"/cwd {sub}")
            assert app.config["cwd"] == str(sub.resolve())
            await slash("/cwd /no/such/dir/here")
            assert app.config["cwd"] == str(sub.resolve())  # unchanged

            # /undo with nothing in the journal is a friendly no-op.
            await slash("/undo")

            # unknown command does not crash.
            await slash("/bogus")

    asyncio.run(main())


def test_multiline_compose_with_ctrl_j_then_enter_submits_full_text():
    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            started: list[str] = []
            app._run_agent = lambda text: started.append(text)  # type: ignore[method-assign]
            inp = app.query_one("#prompt")
            await pilot.press("l", "i", "n", "e", "1")
            await pilot.press("ctrl+j")            # newline, does NOT submit
            await pilot.press("l", "i", "n", "e", "2")
            # A single pause can return before the cascading key->input-change
            # messages drain (flaky under load); pump until the text settles.
            for _ in range(20):
                await pilot.pause()
                if inp.text == "line1\nline2":
                    break
            assert inp.text == "line1\nline2"       # composed two lines
            assert started == []                     # ctrl+j didn't submit
            await pilot.press("enter")               # now submit
            for _ in range(20):
                await pilot.pause()
                if started:
                    break
            assert started == ["line1\nline2"]       # full multi-line text sent
            assert inp.text == ""                    # box cleared

    asyncio.run(main())


def test_up_recalls_history_only_on_first_line():
    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#prompt")
            inp.cmd_history.add("earlier prompt")
            # Two-line draft; cursor on the LAST line → Up moves cursor, no recall.
            inp.text = "top\nbottom"
            inp.move_cursor(inp.document.end)
            await pilot.press("up")
            await pilot.pause()
            assert inp.text == "top\nbottom"          # unchanged (cursor moved)
            # Now cursor is on the first line → Up recalls history.
            await pilot.press("up")
            await pilot.pause()
            assert inp.text == "earlier prompt"

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

            inp.text = "first task"
            await pilot.press("enter")
            await pilot.pause()
            assert started == ["first task"]
            assert app._busy and app._queue == []

            # Second submit while busy → queued, not started.
            inp.text = "second task"
            await pilot.press("enter")
            await pilot.pause()
            assert started == ["first task"]
            assert app._queue == ["second task"]
            assert "1 queued" in app._working_text()

            # When the first turn finishes, the queued one drains automatically.
            app.post_message(AgentFinished())
            await pilot.pause()
            assert started == ["first task", "second task"]
            assert app._queue == []

    asyncio.run(main())


def test_new_user_turn_clears_stale_plan():
    """A pinned plan from a prior task must not linger into an unrelated new
    turn (it was only cleared on /clear). _begin() resets both the panel and
    config['_todo'] so a completed/abandoned plan can't show stale or fire a
    stale continue-nudge. Verified live in a real TUI session 2026-06-24."""
    from textual.widgets import Static

    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            # simulate a plan pinned from a previous task
            app._render_todo("[x] step one\n[x] step two")
            app.config["_todo"] = [("step one", "done"), ("step two", "done")]
            panel = app.query_one("#todo", Static)
            assert "Plan" in str(panel.render())        # plan is showing
            # a new user turn (stub the agent so we don't hit the model)
            app._run_agent = lambda text: None  # type: ignore[method-assign]
            app._begin("an unrelated new request")
            await pilot.pause()
            assert "_todo" not in app.config              # nudge state reset
            assert str(panel.render()) == ""            # panel cleared

    asyncio.run(main())


def test_loop_command_iterates_and_stops():
    """/loop <n> <prompt> re-runs the prompt n times, then clears; Esc ends it."""
    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            begins = []
            app._begin = lambda text: begins.append(text)  # stub out the real agent

            # bad usage → no loop
            app._handle_slash("/loop notanumber")
            assert app._repeat is None and not begins

            # /loop 3 <prompt> → starts, first iteration begun
            app._handle_slash("/loop 3 do the thing")
            assert app._repeat and app._repeat["total"] == 3
            assert begins == ["do the thing"]

            # each finished turn re-begins until the count is exhausted
            from drydock.tui.messages import AgentFinished
            app.on_agent_finished(AgentFinished())  # iter 2
            app.on_agent_finished(AgentFinished())  # iter 3
            assert len(begins) == 3
            app.on_agent_finished(AgentFinished())  # done → loop cleared
            assert app._repeat is None
            assert len(begins) == 3  # no 4th run

            # Esc/stop ends an active loop
            app._begin = lambda text: begins.append(text)
            app._handle_slash("/loop 5 again")
            app._busy = True
            app.action_stop()
            assert app._repeat is None

    asyncio.run(main())


def test_refresh_status_survives_missing_widgets():
    """The 0.18s _tick_work timer can fire one last time during app teardown,
    after the footer widgets (#status/#working) are gone. _refresh_status must
    swallow the resulting NoMatches, not crash the app. Regression for a flaky
    NoMatches('#status') that surfaced under full-suite load."""
    app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
    app._busy = True
    # No widgets are mounted (no run_test), so query_one('#status') would raise
    # NoMatches — the guard must make this a safe no-op.
    app._refresh_status()   # must not raise
    app._tick_work()        # the timer path that triggered the crash; must not raise


def test_context_command_sets_and_persists(tmp_path, monkeypatch):
    """/context <n> updates the live budget AND writes it to config.toml — the
    fix for a stale context_limit (e.g. 32768 left from an old install) that
    silently caps the window."""
    monkeypatch.setenv("HOME", str(tmp_path))

    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp",
                          "context_limit": 32768})
        async with app.run_test() as pilot:
            await pilot.pause()
            app._cmd_context("65536")          # set + persist
            await pilot.pause()
            assert app.config["context_limit"] == 65536
            app._cmd_context("not-a-number")   # invalid → unchanged
            await pilot.pause()
            assert app.config["context_limit"] == 65536

    asyncio.run(main())
    import tomllib
    saved = tomllib.loads((tmp_path / ".drydock" / "config.toml").read_text())
    assert saved["context_limit"] == 65536   # survived to disk


def test_ask_bang_injects_advice_but_plain_ask_does_not():
    """/ask! feeds the advisor's answer into the agent's context (starts a turn
    with it); plain /ask (inject=False) only displays it."""
    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            started: list[str] = []
            app._run_agent = lambda text: started.append(text)  # type: ignore[method-assign]

            # inject=True → a turn begins carrying the advice
            app._deliver_advice("what DS for an LRU cache?",
                                "Use a hash map + a doubly linked list.", True)
            for _ in range(40):
                await pilot.pause()
                if started:
                    break
            assert started, "/ask! should start a turn"
            assert "hash map + a doubly linked list" in started[0]
            assert "advisor model" in started[0]     # framed as a second opinion

            # inject=False → display only, no new turn
            started.clear()
            app._deliver_advice("q", "some answer", False)
            await pilot.pause()
            assert started == []

            # a FAILED consult must not inject even with inject=True
            app._deliver_advice("q", "Could not reach the advisor model at http://x: boom", True)
            await pilot.pause()
            assert started == []

    asyncio.run(main())


def test_stall_watchdog_hint_appears_after_silence():
    """The activity line warns when no agent event has arrived for a while
    (a hung model server) — advisory, and it clears once progress resumes."""
    import time as _time
    from drydock.tui.app import _STALL_HINT_SECS

    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._work_start = _time.monotonic()
            app._work_word = "Working"
            # fresh progress → no hint
            app._last_progress = _time.monotonic()
            assert "stalled" not in app._working_text()
            # simulate silence beyond the threshold → hint appears
            app._last_progress = _time.monotonic() - (_STALL_HINT_SECS + 5)
            assert "stalled" in app._working_text() and "Esc to stop" in app._working_text()
            # progress resumes → hint clears
            app._last_progress = _time.monotonic()
            assert "stalled" not in app._working_text()

    asyncio.run(main())
