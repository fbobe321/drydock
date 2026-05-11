"""Regression: detect_empty_after_tool must skip hallucinated-tool results.

Pre-filter, `empty_after_tool:ralph_repo_index` was the highest-fire
finding in admiral_state (318 fires, 164 sessions) — out-volume-ing
real signals like `empty_after_tool:bash` (150 fires, 85 sessions).

The hallucinated-tool path already has its own recovery wired
(`_silence_suppressed_failures` in agent_loop.py injects a system note
that redirects the model to real tools). The empty turn that follows
is a *different* failure mode and conflating it with real-tool empty
stalls confused both admiral analytics and the Phase 3b proposer.
"""
from __future__ import annotations

from drydock.admiral.detectors_proposed import detect_empty_after_tool
from drydock.core.types import LLMMessage, Role


def _user(text: str) -> LLMMessage:
    return LLMMessage(role=Role.user, content=text)


def _assistant_empty() -> LLMMessage:
    return LLMMessage(role=Role.assistant, content="", tool_calls=[])


def _tool_result(name: str, content: str) -> LLMMessage:
    m = LLMMessage(role=Role.tool, content=content)
    m.name = name  # detect_empty_after_tool reads getattr(prev, "name", "")
    return m


def test_empty_after_real_tool_still_fires() -> None:
    """The detector must still fire on legitimate empty-after-real-tool stalls."""
    msgs = [
        _user("read the config"),
        _tool_result("read_file", "the contents of config.toml"),
        _assistant_empty(),
    ]
    f = detect_empty_after_tool(msgs)
    assert f is not None
    assert f.code == "empty_after_tool:read_file"


def test_empty_after_hallucinated_tool_is_filtered() -> None:
    """`<tool_error>...does not exist...</tool_error>` content marks a
    hallucinated-tool result. Empty turn after it must NOT fire — the
    system-note recovery handles this case."""
    halluc_content = (
        "<tool_error>ralph_repo_index: 'ralph_repo_index' does not exist "
        "— do not call it again. To list project files: call glob...</tool_error>"
    )
    msgs = [
        _user("explore the repo"),
        _tool_result("ralph_repo_index", halluc_content),
        _assistant_empty(),
    ]
    f = detect_empty_after_tool(msgs)
    assert f is None, (
        f"Hallucinated-tool empty turn should NOT fire; got {f.code if f else None}"
    )


def test_empty_after_other_hallucinated_names_filtered() -> None:
    """Same filter applies regardless of hallucinated tool name (
    exit_plan_mode, lsp, etc.)."""
    for name in ("exit_plan_mode", "lsp", "ralph_file_summary", "list_mcp_resources"):
        content = f"<tool_error>{name}: '{name}' does not exist — stop calling it.</tool_error>"
        msgs = [
            _user("go"),
            _tool_result(name, content),
            _assistant_empty(),
        ]
        f = detect_empty_after_tool(msgs)
        assert f is None, f"Should be filtered for hallucinated tool {name!r}; got {f.code if f else None}"


def test_real_tool_with_error_still_fires() -> None:
    """A real tool that errored normally (not via hallucination suppression)
    should still fire — the marker is the specific
    `<tool_error>...does not exist` shape, not just any error."""
    msgs = [
        _user("read it"),
        _tool_result("read_file", "Error: file not found at /tmp/missing.py"),
        _assistant_empty(),
    ]
    f = detect_empty_after_tool(msgs)
    assert f is not None
    assert f.code == "empty_after_tool:read_file"
