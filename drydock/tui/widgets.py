"""Transcript widgets: user turns, streaming assistant text, tool cards."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from textual.message import Message
from textual.widgets import Collapsible, Static, TextArea


# Tools report failure by returning a string with one of these prefixes
# (they return rather than raise, so the agent loop never crashes on a bad
# command). The TUI uses this to mark a card ✓ or ✗.
_FAILURE_PREFIXES = ("Error", "REFUSED")
# Advisory loop/thrash notes are PREPENDED to a tool result as one or more
# "[NOTE: …]" lines (see loop_detect.annotate). Strip them before judging
# success, or a failed Edit behind a "[NOTE: write #9…]" reads as ✓.
_LEADING_NOTES = re.compile(r"^\s*(?:\[NOTE:[^\]]*\]\s*)+")
# Bash appends "[exit code: N]" only when a command FAILED (non-zero return).
# Without this, a failed shell command (e.g. `command not found`, a compile
# error, a failing test) renders as a green ✓ — which hides exactly the kind
# of failure the operator relies on the TUI to surface.
_BASH_EXIT = re.compile(r"\[exit code: (\d+)\]")


def result_is_ok(result: str) -> bool:
    """Whether a tool result should render as success (✓) vs failure (✗)."""
    cleaned = _LEADING_NOTES.sub("", result or "")
    if cleaned.lstrip().startswith(_FAILURE_PREFIXES):
        return False
    m = _BASH_EXIT.search(cleaned)
    if m and m.group(1) != "0":
        return False
    return True


_TOOL_BODY_MAX = 8000


def format_tool_body(result: str) -> str:
    """Prepare a tool result for display in a card.

    Expands tabs to 4-space stops (Rich renders mid-line tabs inconsistently, so
    git status / table output jumbles otherwise) and caps very long output with
    a clear marker so a huge file read can't blow up the card."""
    body = (result or "").expandtabs(4).strip()
    if not body:
        return "(no output)"
    if len(body) > _TOOL_BODY_MAX:
        return body[:_TOOL_BODY_MAX] + (
            f"\n[... {len(body) - _TOOL_BODY_MAX} more chars truncated for display]"
        )
    return body


def flatten_pasted_text(text: str) -> str:
    """Collapse a multi-line paste into one line without losing content.

    Each line is right-stripped and the lines are joined with single spaces,
    dropping blank lines. The result keeps every token the user pasted (unlike
    Textual's default, which discards everything after the first line)."""
    parts = [ln.strip() for ln in text.splitlines()]
    return " ".join(p for p in parts if p)


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


# Single source of truth for the built-in slash commands. Keep in sync with
# app._handle_slash. Drives three things: Tab completion (SLASH_COMMANDS, below),
# the as-you-type discovery menu (slash_menu, below), and — by convention — /help.
# `args` is the short inline hint shown next to the name; `switches` is the
# optional second line detailing sub-commands/flags, revealed once a command is
# chosen so the user can see what it takes.
@dataclass(frozen=True)
class SlashSpec:
    name: str
    args: str = ""
    help: str = ""
    switches: str = ""


SLASH_SPECS: list[SlashSpec] = [
    SlashSpec("/help", "", "list every command"),
    SlashSpec("/model", "[name]", "list or switch models; each routes to its endpoint",
              "/model <name> · add <name> <url> [prov] · default <name> · url <url>"),
    SlashSpec("/cwd", "[path]", "show or change the working directory"),
    SlashSpec("/undo", "", "revert the last file write/edit"),
    SlashSpec("/back", "", "rewind the last turn from the model's context"),
    SlashSpec("/stop", "", "stop the running turn (or press Esc)"),
    SlashSpec("/status", "", "session model, cwd, turns, tokens"),
    SlashSpec("/compact", "", "shrink old context to free up the window"),
    SlashSpec("/context", "[tokens]", "view/set the context-window budget", "/context 65536"),
    SlashSpec("/shell", "", "show which shell the Bash tool runs commands through"),
    SlashSpec("/events", "", "digest of this session's execution trace + governor activity"),
    SlashSpec("/trace", "[N]", "ordered event timeline (last N)"),
    SlashSpec("/resume", "[session_id]", "continue an interrupted session"),
    SlashSpec("/advisor", "", "set up a 2nd 'advisor' model (Gemini etc.)"),
    SlashSpec("/ask", "<q>", "ask the advisor a question (answer shown to you)"),
    SlashSpec("/ask!", "<q>", "ask the advisor and feed the answer to the agent"),
    SlashSpec("/graphrag", "<sub>", "ingest docs into a knowledge base the agent can search",
              "build <path> · add <path> · query <q> · status · clear"),
    SlashSpec("/doc", "<sub>", "drive the Document Canvas tools by hand",
              "open <path> · new <name> ..."),
    SlashSpec("/skills", "[new <name> <prompt>]", "list skills, or create a reusable one"),
    SlashSpec("/loop", "[count|*] <prompt>", "repeat a prompt (1–1000, or * = until Esc)"),
    SlashSpec("/ratchet", "<goal>", "persist verified progress across rounds until tests pass",
              '--verify "<cmd>" to override the auto-detected verifier'),
    SlashSpec("/mcp", "", "list connected MCP servers and their tools"),
    SlashSpec("/rmf", "<sub>", "RMF automation", "bootstrap · control · ..."),
    SlashSpec("/stig", "<sub>", "STIG checklist tooling", "new <xccdf> · summarize · assess"),
    SlashSpec("/clear", "", "reset the conversation"),
    SlashSpec("/quit", "", "exit drydock"),
]

# Flat name list kept for Tab completion (typing "/m" → /model), derived so the
# spec table above stays the one place commands are declared.
SLASH_COMMANDS = [s.name for s in SLASH_SPECS]

_MENU_MAX_ROWS = 14  # cap the list view so a bare "/" doesn't fill the footer


def slash_menu(text: str, skills: tuple[str, ...] = ()) -> str:
    """As-you-type discovery menu for a slash command in progress.

    Returns a formatted, multi-line block for the footer, or "" when the input
    isn't a bare slash command (so a normal message or a "/path/like/this" leaves
    the menu hidden). Two views:

      • list  — while typing the NAME ("/", "/mo"): every matching command with
        its args + one-line help, so the user can see what's available.
      • detail — once a command is chosen (fully typed, or a space started its
        args): that command's usage + switches, so the flags are discoverable too.

    Pure logic (no Textual) so it's unit-testable; the TUI renders the string into
    the #slashmenu Static. `skills` are the user's custom commands, appended live.
    """
    if not text.startswith("/") or "\n" in text:
        return ""
    first, _, _rest = text.partition(" ")
    head = first.lower()
    has_args = " " in text
    specs = list(SLASH_SPECS) + [
        SlashSpec(f"/{n}", "", "custom skill") for n in sorted(skills)
        if f"/{n}" not in {s.name for s in SLASH_SPECS}
    ]
    exact = next((s for s in specs if s.name == head), None)
    matches = [s for s in specs if s.name.startswith(head)]

    # detail view: the command is settled (space-started args, or the sole full match)
    if exact and (has_args or matches == [exact]):
        usage = exact.name + (f" {exact.args}" if exact.args else "")
        lines = [f"  {usage}  —  {exact.help}" if exact.help else f"  {usage}"]
        if exact.switches:
            lines.append(f"    {exact.switches}")
        return "\n".join(lines)

    if not matches:
        return ""
    rows = []
    for s in matches[:_MENU_MAX_ROWS]:
        label = s.name + (f" {s.args}" if s.args else "")
        rows.append(f"  {label:<24}{s.help}" if s.help else f"  {label}")
    extra = len(matches) - _MENU_MAX_ROWS
    if extra > 0:
        rows.append(f"  … +{extra} more — keep typing to filter")
    rows.append("  Tab completes · Enter runs")
    return "\n".join(rows)


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    pre = strings[0]
    for s in strings[1:]:
        while not s.startswith(pre):
            pre = pre[:-1]
    return pre


class PromptArea(TextArea):
    """Multi-line prompt box.

    Enter submits; Ctrl+J inserts a newline so multi-line prompts can be
    composed/pasted. Up/Down navigate within the text normally, EXCEPT when the
    cursor is already on the first line (Up) or last line (Down), where they
    recall command history — the standard editor convention that lets a
    single key serve both jobs without conflict. Multi-line paste is preserved
    natively by TextArea.
    """

    class Submitted(Message):
        """Posted when the user presses Enter to submit the prompt."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cmd_history = PromptHistory()
        self.show_line_numbers = False

    def _recall(self, new_text: str) -> None:
        self.text = new_text
        self.move_cursor(self.document.end)

    async def _on_key(self, event) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if key == "ctrl+j":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == "tab":
            t = self.text
            # empty box + a ghost suggestion → accept it (Claude-Code style)
            if not t and self.placeholder:
                event.stop()
                event.prevent_default()
                self.text = str(self.placeholder or "")
                self.move_cursor(self.document.end)
                return
            if t.startswith("/") and " " not in t and "\n" not in t:
                matches = [c for c in SLASH_COMMANDS if c.startswith(t.lower())]
                if matches:
                    event.stop()
                    event.prevent_default()
                    # one match → complete it (+ space); several → common prefix.
                    self.text = (
                        matches[0] + " " if len(matches) == 1
                        else _common_prefix(matches)
                    )
                    self.move_cursor(self.document.end)
                    return
        if key == "up" and self.cursor_at_first_line:
            event.stop()
            event.prevent_default()
            self._recall(self.cmd_history.up(self.text))
            return
        if key == "down" and self.cursor_at_last_line:
            event.stop()
            event.prevent_default()
            self._recall(self.cmd_history.down(self.text))
            return
        await super()._on_key(event)


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


class ReasoningCard(Collapsible):
    """The model's thinking for a turn — collapsed by default so a long reasoning
    block (gemma runs a 20k-token budget) doesn't flood the transcript; expand
    to read it. Distinct from the answer, which renders as an AssistantMessage."""

    def __init__(self, text: str) -> None:
        self._buf = text
        self._body = Static(text, classes="reasoning-body", markup=False)
        n = len(text)
        super().__init__(
            self._body,
            title=f"💭 thinking  ·  {n:,} chars",
            collapsed=True,
        )
        self.add_class("reasoning-card")

    def append(self, text: str) -> None:
        """Grow the block if a turn streams reasoning in multiple chunks."""
        self._buf += text
        self._body.update(self._buf)
        self.title = f"💭 thinking  ·  {len(self._buf):,} chars"


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
        self._body.update(format_tool_body(result))
        mark = "✓" if ok else "✗"
        self.title = f"{mark} {self._tool_name}  ·  {self._tool_summary}"
        self.add_class("ok" if ok else "fail")
