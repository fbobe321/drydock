"""Interactive checkpoint picker — the UI for `/rewind` with no args.

Two-step flow:
  1. Pick a checkpoint from the list (most recent first).
  2. Pick a restore mode: code, conversation, or both.

Inspired by Claude Code's MessageSelector flow; implementation is from
scratch on top of Textual the way the existing SessionPickerApp is.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from drydock.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

if TYPE_CHECKING:
    from drydock.core.checkpoint import Checkpoint, CheckpointStore


_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400


def _format_relative_time(iso_time: str) -> str:
    """Same shape as session_picker._format_relative_time, but trimmed."""
    if not iso_time:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        delta = (now - dt).total_seconds()
        if delta < _SECONDS_PER_MINUTE:
            return "just now"
        if delta < _SECONDS_PER_HOUR:
            return f"{int(delta // _SECONDS_PER_MINUTE)}m ago"
        if delta < _SECONDS_PER_DAY:
            return f"{int(delta // _SECONDS_PER_HOUR)}h ago"
        return f"{int(delta // _SECONDS_PER_DAY)}d ago"
    except (ValueError, OSError):
        return "?"


def _format_diff_stats(insertions: int, deletions: int,
                       files_changed: int) -> str:
    if not files_changed:
        return "(no change vs current)"
    parts = [f"{files_changed}f"]
    if insertions:
        parts.append(f"+{insertions}")
    if deletions:
        parts.append(f"-{deletions}")
    return " ".join(parts)


def _build_checkpoint_option(cp: "Checkpoint",
                             diff_summary: str) -> Text:
    text = Text(no_wrap=True)
    text.append(f"#{cp.index:>3}  ", style="bold")
    text.append(f"{cp.short_commit()}  ", style="dim")
    text.append(f"{_format_relative_time(cp.timestamp):>10}  ", style="dim")
    text.append(f"msgs={cp.msg_index:<4}  ", style="dim")
    text.append(f"{diff_summary:<22}  ", style="cyan")
    text.append(cp.label[:80])
    return text


class CheckpointPickerApp(Container):
    """First step: pick a checkpoint."""

    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    class CheckpointPicked(Message):
        """User picked a checkpoint — caller advances to mode picker."""
        def __init__(self, checkpoint_index: int) -> None:
            self.checkpoint_index = checkpoint_index
            super().__init__()

    class Cancelled(Message):
        """User hit Esc."""

    def __init__(self, checkpoints: list["Checkpoint"],
                 diff_summaries: dict[int, str], **kwargs: Any) -> None:
        super().__init__(id="checkpoint-picker", **kwargs)
        self._checkpoints = checkpoints
        self._diff_summaries = diff_summaries

    def compose(self) -> ComposeResult:
        options = [
            Option(
                _build_checkpoint_option(
                    cp, self._diff_summaries.get(cp.index, "")
                ),
                id=str(cp.index),
            )
            for cp in self._checkpoints
        ]
        with Vertical(id="checkpoint-picker-content"):
            yield NoMarkupStatic(
                "Select a checkpoint to rewind to (most recent first):",
                classes="checkpoint-picker-header",
            )
            yield OptionList(*options, id="checkpoint-picker-options")
            yield NoMarkupStatic(
                "↑↓ Navigate  Enter Select  Esc Cancel",
                classes="checkpoint-picker-help",
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option.id is not None:
            self.post_message(
                self.CheckpointPicked(int(event.option.id))
            )

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())


class RestoreModePickerApp(Container):
    """Second step: pick what to restore (code / conversation / both)."""

    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    class ModePicked(Message):
        def __init__(self, checkpoint_index: int, mode: str) -> None:
            self.checkpoint_index = checkpoint_index
            self.mode = mode
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self, checkpoint_index: int, label: str,
                 short_commit: str, **kwargs: Any) -> None:
        super().__init__(id="restore-mode-picker", **kwargs)
        self._checkpoint_index = checkpoint_index
        self._label = label
        self._short_commit = short_commit

    def compose(self) -> ComposeResult:
        options = [
            Option(
                Text("Both — restore code AND conversation (recommended)"),
                id="both",
            ),
            Option(
                Text("Code only — restore work-tree files, keep conversation"),
                id="code",
            ),
            Option(
                Text("Conversation only — truncate messages, keep work-tree"),
                id="conversation",
            ),
            Option(Text("Cancel"), id="__cancel__"),
        ]
        with Vertical(id="restore-mode-content"):
            yield NoMarkupStatic(
                f"Rewind to checkpoint #{self._checkpoint_index} "
                f"({self._short_commit}): {self._label[:60]}",
                classes="checkpoint-picker-header",
            )
            yield OptionList(*options, id="restore-mode-options")
            yield NoMarkupStatic(
                "↑↓ Navigate  Enter Select  Esc Cancel",
                classes="checkpoint-picker-help",
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        choice = event.option.id
        if choice in (None, "__cancel__"):
            self.post_message(self.Cancelled())
            return
        self.post_message(
            self.ModePicked(self._checkpoint_index, choice)
        )

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
