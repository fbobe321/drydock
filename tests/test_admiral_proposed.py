"""Unit tests for the proposed Admiral detectors (not-yet-wired)."""
from __future__ import annotations

from drydock.admiral.detectors_proposed import (
    detect_empty_after_tool,
    detect_retry_after_error,
    run_proposed_detectors,
)
from drydock.core.types import FunctionCall, LLMMessage, Role, ToolCall


def _tool(name: str, args: str = "{}") -> LLMMessage:
    return LLMMessage(
        role=Role.assistant,
        content="",
        tool_calls=[ToolCall(function=FunctionCall(name=name, arguments=args))],
    )


def _tool_result(name: str, content: str) -> LLMMessage:
    return LLMMessage(role=Role.tool, content=content, name=name)


def _user(c: str) -> LLMMessage:
    return LLMMessage(role=Role.user, content=c)


def _empty_assistant() -> LLMMessage:
    return LLMMessage(role=Role.assistant, content="", tool_calls=None)


def _text_assistant(c: str) -> LLMMessage:
    return LLMMessage(role=Role.assistant, content=c, tool_calls=None)


# --- detect_empty_after_tool ---------------------------------------------


def test_empty_after_tool_fires_on_empty_assistant() -> None:
    msgs = [
        _user("go"),
        _tool("read_file", '{"path":"x.py"}'),
        _tool_result("read_file", "some output"),
        _empty_assistant(),
    ]
    f = detect_empty_after_tool(msgs)
    assert f is not None
    assert f.code == "empty_after_tool:read_file"
    assert "no content and no tool call" in f.directive


def test_empty_after_tool_fires_on_drydock_filler() -> None:
    """Retrospective case: drydock's filler has replaced the empty assistant."""
    msgs = [
        _user("go"),
        _tool("bash", "{}"),
        _tool_result("bash", "done"),
        _text_assistant("Previous turn ended; awaiting your next instruction."),
    ]
    f = detect_empty_after_tool(msgs)
    assert f is not None
    assert "no content and no tool call" in f.directive


def test_empty_after_tool_does_not_fire_when_assistant_has_content() -> None:
    msgs = [
        _user("go"),
        _tool("bash", "{}"),
        _tool_result("bash", "done"),
        _text_assistant("I have finished the task successfully."),
    ]
    assert detect_empty_after_tool(msgs) is None


def test_empty_after_tool_does_not_fire_after_user_message() -> None:
    msgs = [
        _tool("bash", "{}"),
        _tool_result("bash", "done"),
        _user("continue"),
        _empty_assistant(),
    ]
    assert detect_empty_after_tool(msgs) is None


# --- detect_retry_after_error --------------------------------------------


def test_retry_after_error_fires_on_identical_retry_after_error() -> None:
    msgs = [
        _user("go"),
        _tool("search_replace", '{"path":"x.py","content":"<<<SEARCH>>>"}'),
        _tool_result("search_replace", "Error: pattern not found"),
        _tool("search_replace", '{"path":"x.py","content":"<<<SEARCH>>>"}'),
    ]
    f = detect_retry_after_error(msgs)
    assert f is not None
    assert f.code.startswith("retry_after_error:search_replace")
    assert "same arguments" in f.directive
    assert "Error head" in f.directive


def test_retry_after_error_does_not_fire_when_args_differ() -> None:
    msgs = [
        _user("go"),
        _tool("search_replace", '{"path":"x.py","content":"A"}'),
        _tool_result("search_replace", "Error: not found"),
        _tool("search_replace", '{"path":"x.py","content":"B"}'),
    ]
    assert detect_retry_after_error(msgs) is None


def test_retry_after_error_does_not_fire_when_previous_succeeded() -> None:
    msgs = [
        _user("go"),
        _tool("bash", '{"cmd":"ls"}'),
        _tool_result("bash", "file1 file2"),
        _tool("bash", '{"cmd":"ls"}'),
    ]
    assert detect_retry_after_error(msgs) is None


# --- run_proposed_detectors ---------------------------------------------


def test_run_proposed_returns_both_fires_when_both_apply() -> None:
    # This sequence triggers retry_after_error (two calls, error between)
    # but NOT empty_after_tool (last message is a tool call, not empty).
    msgs = [
        _user("go"),
        _tool("bash", '{"cmd":"bad"}'),
        _tool_result("bash", "Error: syntax error"),
        _tool("bash", '{"cmd":"bad"}'),
    ]
    findings = run_proposed_detectors(msgs)
    codes = [f.code.split(":")[0] for f in findings]
    assert "retry_after_error" in codes


def test_run_proposed_returns_empty_list_on_healthy_sequence() -> None:
    msgs = [
        _user("write hello.py"),
        _tool("write_file", '{"path":"hello.py","content":"print(\'hi\')"}'),
        _tool_result("write_file", "Wrote 19 bytes."),
        _text_assistant("I have created hello.py with a simple print statement."),
    ]
    assert run_proposed_detectors(msgs) == []
