from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import random
from time import time
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from drydock.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from drydock.cli.textual_ui.widgets.spinner import SpinnerMixin, SpinnerType
from drydock.core.drydock_states import get_state_term


def _format_elapsed(seconds: int) -> str:
    if seconds < 60:  # noqa: PLR2004
        return f"{seconds}s"

    minutes, secs = divmod(seconds, 60)
    if minutes < 60:  # noqa: PLR2004
        return f"{minutes}m{secs}s"

    hours, mins = divmod(minutes, 60)
    return f"{hours}h{mins}m{secs}s"


# Rough chars-per-token ratio for English + code. Good enough for a
# liveness indicator — never used for billing or context-window math.
_CHARS_PER_TOKEN = 4


def _format_tokens(n_tokens: int) -> str:
    if n_tokens < 1000:  # noqa: PLR2004
        return f"{n_tokens} tokens"
    if n_tokens < 10_000:  # noqa: PLR2004
        return f"{n_tokens / 1000:.1f}k tokens"
    return f"{n_tokens // 1000}k tokens"


class LoadingWidget(SpinnerMixin, Static):
    TARGET_COLORS = ("#0077B6", "#0096C7", "#00B4D8", "#48CAE4", "#90E0EF")
    SPINNER_TYPE = SpinnerType.WAVE

    EASTER_EGGS: ClassVar[list[str]] = [
        "Hoisting the mainsail",
        "Swabbing the deck",
        "Trimming the sails",
        "Checking the rigging",
        "Scanning the horizon",
        "Reading the tide charts",
        "Polishing the compass",
        "Furling the jib",
        "Splicing the mainbrace",
        "Battening the hatches",
        "Charting the stars",
        "Sounding the depths",
        "Coiling the lines",
    ]

    EASTER_EGGS_HALLOWEEN: ClassVar[list[str]] = [
        "Sailing the ghost ship",
        "Navigating the fog",
        "Summoning Davy Jones",
        "Hunting the Kraken",
        "Haunting the quarterdeck",
    ]

    EASTER_EGGS_DECEMBER: ClassVar[list[str]] = [
        "Decorating the mast",
        "Brewing grog",
        "Navigating by starlight",
        "Anchoring for winter",
        "Stocking the galley",
    ]

    # Minimum seconds between status word changes (prevents flicker)
    _STATUS_CHANGE_INTERVAL = 4.0

    def __init__(self, status: str | None = None) -> None:
        super().__init__(classes="loading-widget")
        self.init_spinner()
        self.status = status or self._get_default_status()
        self.current_color_index = 0
        self._color_direction = 1
        self.transition_progress = 0
        self._status_widget: Static | None = None
        self.hint_widget: Static | None = None
        self.start_time: float | None = None
        self._last_elapsed: int = -1
        self._paused_total: float = 0.0
        self._pause_start: float | None = None
        self._last_status_change: float = 0.0
        # Streaming-liveness counter. Counts characters (cheap) and
        # converts to an estimated token count for display so the user
        # can see the LLM is actively producing output even when the
        # elapsed-seconds tick is too coarse to feel "alive."
        self._streamed_chars: int = 0
        self._last_displayed_tokens: int = -1

    def _get_easter_egg(self) -> str | None:
        EASTER_EGG_PROBABILITY = 0.10
        if random.random() < EASTER_EGG_PROBABILITY:
            available_eggs = list(self.EASTER_EGGS)

            OCTOBER = 10
            HALLOWEEN_DAY = 31
            DECEMBER = 12
            now = datetime.now()
            if now.month == OCTOBER and now.day == HALLOWEEN_DAY:
                available_eggs.extend(self.EASTER_EGGS_HALLOWEEN)
            if now.month == DECEMBER:
                available_eggs.extend(self.EASTER_EGGS_DECEMBER)

            return random.choice(available_eggs)
        return None

    def _get_default_status(self) -> str:
        return self._get_easter_egg() or f"\u2693 {get_state_term('reason')}"

    def _apply_easter_egg(self, status: str) -> str:
        return self._get_easter_egg() or status

    def pause_timer(self) -> None:
        if self._pause_start is None:
            self._pause_start = time()

    def resume_timer(self) -> None:
        if self._pause_start is not None:
            self._paused_total += time() - self._pause_start
            self._pause_start = None

    def add_streamed_chars(self, n: int) -> None:
        if n > 0:
            self._streamed_chars += n

    def set_status(self, status: str) -> None:
        # Throttle status word changes to prevent flicker
        now = time()
        if now - self._last_status_change < self._STATUS_CHANGE_INTERVAL:
            return  # Too soon — keep current word
        self._last_status_change = now
        self.status = self._apply_easter_egg(status)
        self._update_animation()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="loading-container"):
            self._indicator_widget = Static(
                self._spinner.current_frame(), classes="loading-indicator"
            )
            yield self._indicator_widget

            self._status_widget = Static("", classes="loading-status")
            yield self._status_widget

            self.hint_widget = NoMarkupStatic(
                "(0s esc to interrupt)", classes="loading-hint"
            )
            yield self.hint_widget

    def on_mount(self) -> None:
        self.start_time = time()
        self._update_animation()
        self.start_spinner_timer()

    def on_resize(self) -> None:
        self.refresh_spinner()

    def _update_spinner_frame(self) -> None:
        if not self._is_spinning:
            return
        self._update_animation()

    def _next_color_index(self) -> int:
        return self.current_color_index + self._color_direction

    def _get_color_for_position(self, position: int) -> str:
        current_color = self.TARGET_COLORS[self.current_color_index]
        next_color = self.TARGET_COLORS[self._next_color_index()]
        if position < self.transition_progress:
            return next_color
        return current_color

    def _build_status_text(self) -> str:
        parts = []
        for i, char in enumerate(self.status):
            color = self._get_color_for_position(1 + i)
            parts.append(f"[{color}]{char}[/]")
        ellipsis_start = 1 + len(self.status)
        color_ellipsis = self._get_color_for_position(ellipsis_start)
        parts.append(f"[{color_ellipsis}]… [/]")
        return "".join(parts)

    def _update_animation(self) -> None:
        total_elements = 1 + len(self.status) + 1

        if self._indicator_widget:
            spinner_char = self._spinner.next_frame()
            color = self._get_color_for_position(0)
            self._indicator_widget.update(f"[{color}]{spinner_char}[/]")

        if self._status_widget:
            self._status_widget.update(self._build_status_text())

        self.transition_progress += 1
        if self.transition_progress > total_elements:
            self.current_color_index = self._next_color_index()
            if not 0 < self.current_color_index < len(self.TARGET_COLORS) - 1:
                self._color_direction *= -1
            self.transition_progress = 0

        if self.hint_widget and self.start_time is not None:
            paused = self._paused_total + (
                time() - self._pause_start if self._pause_start else 0
            )
            elapsed = int(time() - self.start_time - paused)
            tokens = self._streamed_chars // _CHARS_PER_TOKEN
            if elapsed != self._last_elapsed or tokens != self._last_displayed_tokens:
                self._last_elapsed = elapsed
                self._last_displayed_tokens = tokens
                if tokens > 0:
                    hint = (
                        f"({_format_elapsed(elapsed)} ↓ "
                        f"{_format_tokens(tokens)} · esc to interrupt)"
                    )
                else:
                    hint = f"({_format_elapsed(elapsed)} esc to interrupt)"
                self.hint_widget.update(hint)


@contextmanager
def paused_timer(loading_widget: LoadingWidget | None) -> Iterator[None]:
    if loading_widget:
        loading_widget.pause_timer()
    try:
        yield
    finally:
        if loading_widget:
            loading_widget.resume_timer()
