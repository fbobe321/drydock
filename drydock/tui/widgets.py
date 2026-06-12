"""Transcript widgets: user turns, streaming assistant text, tool cards."""
from __future__ import annotations

from pathlib import Path

from textual.widgets import Collapsible, Input, Static


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


class PromptHistory:
    """Shell-style command history for the prompt box.

    Up walks toward older entries, Down toward newer; stepping past the
    newest restores whatever draft the user was typing before they started
    navigating. Consecutive duplicate submissions collapse to one entry.

    If `path` is given, history persists across sessions: existing entries are
    loaded on construction and the (capped) list is rewritten on every add.
    Persistence failures are swallowed — a broken history file must never stop
    the agent from running.
    """

    def __init__(self, path: Path | None = None, max_entries: int = 1000) -> None:
        self._path = path
        self._max = max_entries
        self._items: list[str] = self._load()
        self._idx: int | None = None  # None ⇒ not navigating (on the draft)
        self._draft: str = ""

    def _load(self) -> list[str]:
        if not self._path or not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [ln for ln in lines if ln.strip()][-self._max:]

    def _persist(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                "\n".join(self._items) + "\n", encoding="utf-8"
            )
        except OSError:
            pass

    def add(self, text: str) -> None:
        # Persisted format is line-based; collapse any newlines so a pasted
        # multi-line prompt can't corrupt the file (and the entry stays
        # recallable as one line).
        text = " ".join(text.split())
        if text and not (self._items and self._items[-1] == text):
            self._items.append(text)
            if len(self._items) > self._max:
                self._items = self._items[-self._max:]
            self._persist()
        self._idx = None
        self._draft = ""

    def up(self, current: str) -> str:
        """Older entry. `current` is saved as the draft on first step up."""
        if not self._items:
            return current
        if self._idx is None:
            self._draft = current
            self._idx = len(self._items) - 1
        elif self._idx > 0:
            self._idx -= 1
        return self._items[self._idx]

    def down(self, current: str) -> str:
        """Newer entry, or the saved draft once past the newest."""
        if self._idx is None:
            return current  # not navigating — leave the line alone
        if self._idx < len(self._items) - 1:
            self._idx += 1
            return self._items[self._idx]
        self._idx = None
        return self._draft


class PromptInput(Input):
    """Single-line prompt with Up/Down command-history recall."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.history = PromptHistory()

    def on_key(self, event) -> None:
        if event.key not in ("up", "down"):
            return
        self.value = (
            self.history.up(self.value) if event.key == "up"
            else self.history.down(self.value)
        )
        self.cursor_position = len(self.value)
        event.stop()
        event.prevent_default()


# markup=False everywhere in the transcript: user prompts, model output, tool
# results and file paths routinely contain brackets (list[int], [INFO], tracebacks)
# that Textual would otherwise parse as console markup — mangling text or raising
# MarkupError on malformed tags.

class UserMessage(Static):
    """A user's prompt."""

    def __init__(self, text: str) -> None:
        super().__init__(f"❯ {text}", classes="user-msg", markup=False)


class AssistantMessage(Static):
    """Streamed assistant text; grows as chunks arrive."""

    def __init__(self) -> None:
        super().__init__("", classes="assistant-msg", markup=False)
        self._buf = ""

    def append(self, text: str) -> None:
        self._buf += text
        self.update(self._buf)

    @property
    def is_empty(self) -> bool:
        return not self._buf.strip()


class ErrorMessage(Static):
    def __init__(self, text: str) -> None:
        super().__init__(f"⚠ {text}", classes="error-msg", markup=False)


class ToolCard(Collapsible):
    """A tool call: compact header (name + summary), expandable full output."""

    def __init__(self, name: str, summary: str) -> None:
        self._body = Static("…running", classes="tool-body", markup=False)
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
