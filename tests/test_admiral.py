"""Unit tests for Admiral's Phase 1 detectors."""
from __future__ import annotations

from drydock.admiral.detectors import detect_struggle, detect_tool_call_loop, run_all
from drydock.core.types import FunctionCall, LLMMessage, Role, ToolCall


def _assistant(tool: str, args: str = "{}") -> LLMMessage:
    return LLMMessage(
        role=Role.assistant,
        content="",
        tool_calls=[ToolCall(function=FunctionCall(name=tool, arguments=args))],
    )


def test_loop_fires_on_three_identical_tool_calls() -> None:
    msgs = [
        LLMMessage(role=Role.user, content="go"),
        _assistant("read_file", '{"path":"x.py"}'),
        LLMMessage(role=Role.tool, content="result"),
        _assistant("read_file", '{"path":"x.py"}'),
        LLMMessage(role=Role.tool, content="result"),
        _assistant("read_file", '{"path":"x.py"}'),
        LLMMessage(role=Role.tool, content="result"),
    ]
    f = detect_tool_call_loop(msgs)
    assert f is not None
    assert "Admiral" in f.directive
    assert f.code.startswith("loop:")


def test_loop_no_fire_on_varied_args() -> None:
    msgs = [
        _assistant("read_file", '{"path":"x.py"}'),
        LLMMessage(role=Role.tool, content="r"),
        _assistant("read_file", '{"path":"y.py"}'),
        LLMMessage(role=Role.tool, content="r"),
        _assistant("read_file", '{"path":"z.py"}'),
        LLMMessage(role=Role.tool, content="r"),
    ]
    assert detect_tool_call_loop(msgs) is None


def test_struggle_fires_after_many_reads_without_write() -> None:
    msgs: list[LLMMessage] = [LLMMessage(role=Role.user, content="build it")]
    for i in range(25):
        msgs.append(_assistant("read_file", f'{{"path":"f{i}.py"}}'))
        msgs.append(LLMMessage(role=Role.tool, content="..."))
    f = detect_struggle(msgs)
    assert f is not None
    assert "without writing" in f.directive


def test_struggle_resets_on_write() -> None:
    msgs: list[LLMMessage] = []
    for i in range(15):
        msgs.append(_assistant("read_file", f'{{"path":"f{i}.py"}}'))
        msgs.append(LLMMessage(role=Role.tool, content="..."))
    msgs.append(_assistant("write_file", '{"path":"new.py","content":"x"}'))
    msgs.append(LLMMessage(role=Role.tool, content="ok"))
    for i in range(3):
        msgs.append(_assistant("read_file", f'{{"path":"check{i}.py"}}'))
        msgs.append(LLMMessage(role=Role.tool, content="..."))
    assert detect_struggle(msgs) is None


def test_run_all_returns_multiple_findings() -> None:
    msgs: list[LLMMessage] = []
    for i in range(25):
        msgs.append(_assistant("read_file", '{"path":"x.py"}'))  # same args -> loop
        msgs.append(LLMMessage(role=Role.tool, content="..."))
    findings = run_all(msgs)
    codes = {f.code.split(":")[0] for f in findings}
    assert "loop" in codes
    assert "struggle" in codes
