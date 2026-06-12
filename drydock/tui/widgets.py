"""Transcript widgets: user turns, streaming assistant text, tool cards."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Collapsible, Static


# Tools report failure by returning a string with one of these prefixes
# (they return rather than raise, so the agent loop never crashes on a bad
# command). The TUI uses this to mark a card ✓ or ✗.
_FAILURE_PREFIXES = ("Error", "REFUSED")


def result_is_ok(result: str) -> bool:
    """Whether a tool result should render as success (✓) vs failure (✗)."""
    return not result.lstrip().startswith(_FAILURE_PREFIXES)


def summarize_inputs(inputs: dict) -> str:
    """Short one-line summary of tool inputs for a card header."""
    for key in ("command", "file_path", "pattern", "path"):
        if key in inputs and inputs[key]:
            val = str(inputs[key])
            return val[:80] + ("…" if len(val) > 80 else "")
    s = str(inputs)
    return s[:80] + ("…" if len(s) > 80 else "")


class UserMessage(Static):
    """A user's prompt."""

    def __init__(self, text: str) -> None:
        super().__init__(f"❯ {text}", classes="user-msg")


class AssistantMessage(Static):
    """Streamed assistant text; grows as chunks arrive."""

    def __init__(self) -> None:
        super().__init__("", classes="assistant-msg")
        self._buf = ""

    def append(self, text: str) -> None:
        self._buf += text
        self.update(self._buf)

    @property
    def is_empty(self) -> bool:
        return not self._buf.strip()


class ErrorMessage(Static):
    def __init__(self, text: str) -> None:
        super().__init__(f"⚠ {text}", classes="error-msg")


class ToolCard(Collapsible):
    """A tool call: compact header (name + summary), expandable full output."""

    def __init__(self, name: str, summary: str) -> None:
        self._body = Static("…running", classes="tool-body")
        super().__init__(
            self._body,
            title=f"⚓ {name}  ·  {summary}",
            collapsed=True,
        )
        # Set after super().__init__ — Textual's DOMNode owns ._name.
        self._tool_name = name
        self._tool_summary = summary
        self.add_class("tool-card")

    def finish(self, result: str, ok: bool = True) -> None:
        self._body.update(result.strip() or "(no output)")
        mark = "✓" if ok else "✗"
        self.title = f"{mark} {self._tool_name}  ·  {self._tool_summary}"
        self.add_class("ok" if ok else "fail")
