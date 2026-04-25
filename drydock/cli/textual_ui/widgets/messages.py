from __future__ import annotations

import re
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static
from textual.widgets._markdown import MarkdownStream

from drydock.cli.textual_ui.ansi_markdown import AnsiMarkdown as Markdown
from drydock.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from drydock.cli.textual_ui.widgets.spinner import SpinnerMixin, SpinnerType


class NonSelectableStatic(NoMarkupStatic):
    @property
    def text_selection(self) -> None:
        return None

    @text_selection.setter
    def text_selection(self, value: Any) -> None:
        pass

    def get_selection(self, selection: Any) -> None:
        return None


class ExpandingBorder(NonSelectableStatic):
    def render(self) -> str:
        height = self.size.height
        return "\n".join(["⎢"] * (height - 1) + ["⎣"])

    def on_resize(self) -> None:
        self.refresh()


class UserMessage(Static):
    def __init__(self, content: str, pending: bool = False) -> None:
        super().__init__()
        self.add_class("user-message")
        self._content = content
        self._pending = pending

    def compose(self) -> ComposeResult:
        with Horizontal(classes="user-message-container"):
            yield NoMarkupStatic(self._content, classes="user-message-content")
            if self._pending:
                self.add_class("pending")

    async def set_pending(self, pending: bool) -> None:
        if pending == self._pending:
            return

        self._pending = pending

        if pending:
            self.add_class("pending")
            return

        self.remove_class("pending")


class StreamingMessageBase(Static):
    def __init__(self, content: str) -> None:
        super().__init__()
        self._content = content
        self._markdown: Markdown | None = None
        self._stream: MarkdownStream | None = None
        self._content_initialized = False

    def _get_markdown(self) -> Markdown:
        if self._markdown is None:
            raise RuntimeError(
                "Markdown widget not initialized. compose() must be called first."
            )
        return self._markdown

    def _ensure_stream(self) -> MarkdownStream:
        if self._stream is None:
            self._stream = Markdown.get_stream(self._get_markdown())
        return self._stream

    async def append_content(self, content: str) -> None:
        if not content:
            return

        self._content += content
        if self._should_write_content():
            stream = self._ensure_stream()
            await stream.write(content)

    async def write_initial_content(self) -> None:
        if self._content_initialized:
            return
        if self._content and self._should_write_content():
            stream = self._ensure_stream()
            await stream.write(self._content)

    async def stop_stream(self) -> None:
        if self._stream is None:
            return

        await self._stream.stop()
        self._stream = None

    def _should_write_content(self) -> bool:
        return True

    def is_stripped_content_empty(self) -> bool:
        return self._content.strip() == ""


_INLINE_ENUM_RE = re.compile(r" (?=\d{1,2}\. [A-Z])")
_INLINE_HEADING_RE = re.compile(r" (?=\*\*[A-Z][A-Za-z ]{1,20}:\*\*)")
_INLINE_DASH_BULLET_RE = re.compile(r"(?<=[.!?]) (?=- [A-Z])")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?]) (?=[A-Z])")
_INLINE_CAPWORD_HEADING_RE = re.compile(
    r"(?<=[.!?]) (?=[A-Z][A-Za-z]{1,15}:\s)"
)


def _sentence_chunks(text: str, group: int = 2) -> str:
    """Break a long flat string into paragraphs every `group` sentences.

    Conservative: only fires when there are at least 2 sentences to
    split between. Splits on real sentence boundaries (`. `, `! `, `? `
    followed by a capital).
    """
    sentences = _SENTENCE_BOUNDARY_RE.split(text)
    if len(sentences) < 2:
        return text
    paragraphs: list[str] = []
    for i in range(0, len(sentences), group):
        paragraphs.append(" ".join(sentences[i:i + group]))
    return "\n\n".join(paragraphs)


def _break_walls_of_text(text: str) -> str:
    """Make flat, low-newline assistant responses readable.

    Gemma 4 regularly emits responses with zero `\\n` — either a block
    of enumerated items jammed together (`Changes: 1. A 2. B 3. C`) or
    a long paragraph of prose (`I fixed X. The Y class now uses Z.
    All tests pass.`). The Markdown widget renders both as one line.

    Strategy, applied in order:
    1. Break before inline `**Heading:**` markers.
    2. Break before inline numbered items (` 1. Foo 2. Bar`).
    3. Break before inline `CapWord:` mini-headings (`Changes: ...`).
    4. If still a wall (long + zero newlines), split every 2 sentences
       at real sentence boundaries so readers get paragraph breaks.

    Conservative guards: nothing fires unless the chunk is >200 chars
    and has fewer than 3 newlines. Short responses and already-
    structured markdown pass through untouched.
    """
    if not text or len(text) < 200:
        return text
    nl = text.count("\n")
    if nl >= 3:
        return text
    out = _INLINE_HEADING_RE.sub("\n\n", text)
    out = _INLINE_ENUM_RE.sub("\n", out)
    out = _INLINE_DASH_BULLET_RE.sub("\n", out)
    out = _INLINE_CAPWORD_HEADING_RE.sub("\n\n", out)
    # If inline markers didn't produce any breaks AND the text is long
    # enough that one paragraph hurts readability, split at sentence
    # boundaries. group=1 = each sentence on its own paragraph (most
    # aggressive — user-requested in issue #8 reopen). Threshold is
    # 250 so medium-length responses also get paragraphs.
    if "\n" not in out and len(out) > 250:
        out = _sentence_chunks(out, group=1)
    return out


def _preserve_line_breaks(text: str) -> str:
    """Promote single \\n to markdown paragraph-break (\\n\\n) so the
    rendered output keeps line breaks the model wrote.

    Markdown spec treats single \\n as whitespace; only \\n\\n is a
    paragraph break. Gemma 4 emits assistant text with single-newline
    lists/steps that disappear when rendered. The previous attempt
    used trailing-spaces hard-break (`  \\n`) but Textual's Markdown
    widget rendered those as visible double-spaces, making prose look
    run-on. Doubling the newline preserves visual breaks AND renders
    cleanly.

    Also handles the "wall of text" case (issue #8): model emits a
    long flat string with no newlines but inline numbered lists and
    bold headings. We inject newlines before those markers so the
    Markdown widget renders them as real blocks.

    Skips: code fences (preserved verbatim), markdown lists (already
    render with proper spacing), and any line ending in a trailing
    backslash (markdown escape).
    """
    if not text:
        return text
    # First pass: rescue wall-of-text responses (flat prose, inline
    # enumerations, inline bold headings). See _break_walls_of_text.
    text = _break_walls_of_text(text)
    out: list[str] = []
    in_fence = False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        out.append(line)
        if in_fence:
            continue
        if i == len(lines) - 1:
            continue
        nxt = lines[i + 1]
        # Skip if already a paragraph break, or current/next is empty.
        if line == "" or nxt == "":
            continue
        # Don't insert blank between adjacent list items — markdown
        # already renders those on separate lines.
        stripped = line.lstrip()
        nxt_stripped = nxt.lstrip()
        if (stripped.startswith(("- ", "* ", "+ "))
                and nxt_stripped.startswith(("- ", "* ", "+ "))):
            continue
        if stripped[:2].rstrip(".").isdigit() and nxt_stripped[:2].rstrip(".").isdigit():
            continue  # numbered list
        # Insert blank line for paragraph break.
        out.append("")
    return "\n".join(out)


class AssistantMessage(StreamingMessageBase):
    def __init__(self, content: str) -> None:
        super().__init__(_preserve_line_breaks(content))
        self.add_class("assistant-message")

    async def append_content(self, content: str) -> None:
        # _preserve_line_breaks doubles \n → \n\n WITHIN a chunk. Streaming
        # chunks routinely split on the newline boundary ("abc\n" then
        # "def"), which that function can't upgrade — the single \n stays
        # and markdown renders it as a space, collapsing lists and
        # paragraphs. We bridge the gap here by checking the join point:
        # if the accumulated content ends with a single \n and the new
        # chunk starts with non-whitespace, inject an extra \n so markdown
        # sees the paragraph break. Handles issue #5 (v2.6.130).
        prepared = _preserve_line_breaks(content)
        if (
            prepared
            and self._content.endswith("\n")
            and not self._content.endswith("\n\n")
            and not self._content.endswith("\t\n")
            and prepared[0] not in ("\n", " ", "\t")
        ):
            prepared = "\n" + prepared
        await super().append_content(prepared)

    async def stop_stream(self) -> None:
        # Issue #12: when a Gemma 4 response streams in chunks too small
        # to trip _break_walls_of_text's >200-char per-chunk threshold,
        # the final accumulated content can be a long no-newline blob —
        # paragraph breaks, inline headings, and numbered list items all
        # rolled into one wall. Apply wall-rescue once at stream end on
        # the full content; if it injected paragraph structure, replay
        # the rescued text into the markdown widget.
        if (
            self._stream is not None
            and self._markdown is not None
            and self._content
        ):
            rescued = _break_walls_of_text(self._content)
            if rescued != self._content:
                await self._stream.stop()
                self._stream = None
                self._content = rescued
                await self._markdown.update("")
                stream = self._ensure_stream()
                await stream.write(rescued)
        await super().stop_stream()

    def compose(self) -> ComposeResult:
        if self._content:
            self._content_initialized = True
        markdown = Markdown(self._content)
        self._markdown = markdown
        yield markdown


class ReasoningMessage(SpinnerMixin, StreamingMessageBase):
    SPINNER_TYPE = SpinnerType.PULSE
    SPINNING_TEXT = "Thinking"
    COMPLETED_TEXT = "Thought"

    def __init__(self, content: str, collapsed: bool = True) -> None:
        super().__init__(content)
        self.add_class("reasoning-message")
        self.collapsed = collapsed
        self._indicator_widget: Static | None = None
        self._triangle_widget: Static | None = None
        self.init_spinner()

    def compose(self) -> ComposeResult:
        with Vertical(classes="reasoning-message-wrapper"):
            with Horizontal(classes="reasoning-message-header"):
                self._indicator_widget = NonSelectableStatic(
                    self._spinner.current_frame(), classes="reasoning-indicator"
                )
                yield self._indicator_widget
                self._status_text_widget = NoMarkupStatic(
                    self.SPINNING_TEXT, classes="reasoning-collapsed-text"
                )
                yield self._status_text_widget
                self._triangle_widget = NonSelectableStatic(
                    "▶" if self.collapsed else "▼", classes="reasoning-triangle"
                )
                yield self._triangle_widget
            markdown = Markdown("", classes="reasoning-message-content")
            markdown.display = not self.collapsed
            self._markdown = markdown
            yield markdown

    def on_mount(self) -> None:
        self.start_spinner_timer()

    def on_resize(self) -> None:
        self.refresh_spinner()

    async def on_click(self) -> None:
        await self._toggle_collapsed()

    async def _toggle_collapsed(self) -> None:
        await self.set_collapsed(not self.collapsed)

    def _should_write_content(self) -> bool:
        return not self.collapsed

    async def set_collapsed(self, collapsed: bool) -> None:
        if self.collapsed == collapsed:
            return

        self.collapsed = collapsed
        if self._triangle_widget:
            self._triangle_widget.update("▶" if collapsed else "▼")
        if self._markdown:
            self._markdown.display = not collapsed
            if not collapsed and self._content:
                if self._stream is not None:
                    await self._stream.stop()
                    self._stream = None
                await self._markdown.update("")
                stream = self._ensure_stream()
                await stream.write(self._content)


class UserCommandMessage(Static):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.add_class("user-command-message")
        self._content = content

    def compose(self) -> ComposeResult:
        with Horizontal(classes="user-command-container"):
            yield ExpandingBorder(classes="user-command-border")
            with Vertical(classes="user-command-content"):
                yield Markdown(self._content)


class WhatsNewMessage(Static):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.add_class("whats-new-message")
        self._content = content

    def compose(self) -> ComposeResult:
        yield Markdown(self._content)


class InterruptMessage(Static):
    def __init__(self) -> None:
        super().__init__()
        self.add_class("interrupt-message")

    def compose(self) -> ComposeResult:
        with Horizontal(classes="interrupt-container"):
            yield ExpandingBorder(classes="interrupt-border")
            yield NoMarkupStatic(
                "Interrupted · What should Drydock do instead?",
                classes="interrupt-content",
            )


class BashOutputMessage(Static):
    def __init__(self, command: str, cwd: str, output: str, exit_code: int) -> None:
        super().__init__()
        self.add_class("bash-output-message")
        self._command = command
        self._cwd = cwd
        self._output = output.rstrip("\n")
        self._exit_code = exit_code

    def compose(self) -> ComposeResult:
        status_class = "bash-success" if self._exit_code == 0 else "bash-error"
        self.add_class(status_class)
        with Horizontal(classes="bash-command-line"):
            yield NonSelectableStatic("$ ", classes=f"bash-prompt {status_class}")
            yield NoMarkupStatic(self._command, classes="bash-command")
        with Horizontal(classes="bash-output-container"):
            yield ExpandingBorder(classes="bash-output-border")
            yield NoMarkupStatic(self._output, classes="bash-output")


class ErrorMessage(Static):
    def __init__(self, error: str, collapsed: bool = False) -> None:
        super().__init__()
        self.add_class("error-message")
        self._error = error
        self.collapsed = collapsed
        self._content_widget: Static | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="error-container"):
            yield ExpandingBorder(classes="error-border")
            self._content_widget = NoMarkupStatic(
                f"Error: {self._error}", classes="error-content"
            )
            yield self._content_widget

    def set_collapsed(self, collapsed: bool) -> None:
        pass


class WarningMessage(Static):
    def __init__(self, message: str, show_border: bool = True) -> None:
        super().__init__()
        self.add_class("warning-message")
        self._message = message
        self._show_border = show_border

    def compose(self) -> ComposeResult:
        with Horizontal(classes="warning-container"):
            if self._show_border:
                yield ExpandingBorder(classes="warning-border")
            yield NoMarkupStatic(self._message, classes="warning-content")
