"""Regression tests for struggle detector dedup fix.

Before the fix, the finding code was f"struggle:{count}:{tool}" — each count
was a unique key, bypassing DEDUP_WINDOW_SEC and producing 33 identical
interventions in one session that the model ignored.

After the fix, the code is stable (f"struggle:{tool}") so a single entry
in recent_findings suppresses repeat firings within the 60-second window.
"""
from __future__ import annotations

from drydock.admiral.detectors import detect_struggle
from drydock.core.types import FunctionCall, LLMMessage, Role, ToolCall


def _assistant(tool: str, args: str = "{}") -> LLMMessage:
    return LLMMessage(
        role=Role.assistant,
        content="",
        tool_calls=[ToolCall(function=FunctionCall(name=tool, arguments=args))],
    )


def _make_msgs(write_tool: str, non_write_count: int) -> list[LLMMessage]:
    msgs: list[LLMMessage] = [LLMMessage(role=Role.user, content="build it")]
    msgs.append(_assistant(write_tool, '{"path":"x.py","content":"x"}'))
    msgs.append(LLMMessage(role=Role.tool, content="ok"))
    for i in range(non_write_count):
        msgs.append(_assistant("read_file", f'{{"path":"f{i}.py"}}'))
        msgs.append(LLMMessage(role=Role.tool, content="content"))
    return msgs


def test_struggle_code_is_stable_no_count() -> None:
    """Finding code must NOT include the count so dedup works across counts."""
    msgs20 = _make_msgs("search_replace", 20)
    msgs33 = _make_msgs("search_replace", 33)
    f20 = detect_struggle(msgs20)
    f33 = detect_struggle(msgs33)
    assert f20 is not None
    assert f33 is not None
    # Same stable code regardless of count
    assert f20.code == f33.code
    assert "20" not in f20.code
    assert "33" not in f33.code


def test_struggle_escalated_directive_at_30() -> None:
    """At 30+ non-write calls, directive must mention write_file overwrite."""
    msgs = _make_msgs("search_replace", 30)
    f = detect_struggle(msgs)
    assert f is not None
    assert "write_file" in f.directive
    assert "overwrite" in f.directive


def test_struggle_gentle_directive_below_30() -> None:
    """Below 30, directive is the gentle nudge (no overwrite mention)."""
    msgs = _make_msgs("search_replace", 22)
    f = detect_struggle(msgs)
    assert f is not None
    assert "overwrite" not in f.directive
    assert "commit to a plan" in f.directive


def test_struggle_no_fire_below_threshold() -> None:
    msgs = _make_msgs("write_file", 5)
    assert detect_struggle(msgs) is None


def _make_msgs_no_write(non_write_count: int) -> list[LLMMessage]:
    """Messages where the model never called any write tool."""
    msgs: list[LLMMessage] = [LLMMessage(role=Role.user, content="build it")]
    for i in range(non_write_count):
        msgs.append(_assistant("read_file", f'{{"path":"f{i}.py"}}'))
        msgs.append(LLMMessage(role=Role.tool, content="content"))
    return msgs


def test_struggle_none_escalated_says_start_writing() -> None:
    """When model never wrote a file and hits 30+ calls, directive must not
    mention search_replace (model never used it) and must urge writing."""
    msgs = _make_msgs_no_write(30)
    f = detect_struggle(msgs)
    assert f is not None
    assert f.code == "struggle:none"
    assert "search_replace" not in f.directive
    assert "write_file" in f.directive
    assert "exploring" in f.directive or "STOP" in f.directive


def test_struggle_with_prior_write_escalated_mentions_search_replace() -> None:
    """When model previously wrote and is now stuck retrying, the search_replace
    message is appropriate."""
    msgs = _make_msgs("search_replace", 30)
    f = detect_struggle(msgs)
    assert f is not None
    assert "search_replace" in f.directive
    assert "overwrite" in f.directive
