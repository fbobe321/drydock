"""Tests for /back (rewind last turn) and drop_last_turn."""
from __future__ import annotations

import asyncio

from drydock.agent import drop_last_turn
from drydock.tui.app import DrydockApp


def _two_turn_history() -> list:
    return [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "a", "name": "Bash", "input": {"command": "ls"}}]},
        {"role": "tool", "tool_call_id": "a", "name": "Bash", "content": "out"},
        {"role": "assistant", "content": "done first"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "b", "name": "Bash", "input": {"command": "pwd"}}]},
        {"role": "tool", "tool_call_id": "b", "name": "Bash", "content": "/tmp"},
        {"role": "assistant", "content": "done second"},
    ]


def test_drop_last_turn_cuts_at_user_boundary():
    msgs = _two_turn_history()
    assert drop_last_turn(msgs) is True
    # Everything from the 2nd user message onward is gone; 1st turn intact.
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[0]["content"] == "first"
    # No orphaned tool result (every tool still follows its assistant tool_call).
    for i, m in enumerate(msgs):
        if m["role"] == "tool":
            assert msgs[i - 1]["role"] == "assistant"


def test_drop_last_turn_twice_empties_history():
    msgs = _two_turn_history()
    assert drop_last_turn(msgs) is True
    assert drop_last_turn(msgs) is True
    assert msgs == []
    assert drop_last_turn(msgs) is False  # nothing left


def test_back_command_rewinds_in_app():
    async def main():
        app = DrydockApp({"model": "gemma4", "provider": "vllm", "cwd": "/tmp"})
        async with app.run_test() as pilot:
            await pilot.pause()
            app.state.messages = _two_turn_history()
            inp = app.query_one("#prompt")
            inp.text = "/back"
            await pilot.press("enter")
            await pilot.pause()
            assert [m["content"] for m in app.state.messages if m["role"] == "user"] == ["first"]

    asyncio.run(main())
