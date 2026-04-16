"""Cloud-vs-local choice screen — step 2 of onboarding.

Asks the user which kind of LLM they're connecting to. Answers github
issue #3: a fresh install used to drop straight into a Mistral API key
prompt with no obvious way to use a local vLLM instead. Now the user
sees the choice up front.
"""
from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Vertical
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from drydock.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from drydock.setup.onboarding.base import OnboardingScreen


class ChoiceScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = None  # set dynamically based on selection

    def compose(self) -> ComposeResult:
        with Vertical(id="choice-outer"):
            yield NoMarkupStatic("", classes="spacer")
            yield Center(NoMarkupStatic(
                "How will you connect to an LLM?",
                id="choice-title",
            ))
            with Center():
                with Vertical(id="choice-content"):
                    yield OptionList(
                        Option(
                            "Local server (vLLM, Ollama, LM Studio, llama.cpp)",
                            id="local",
                        ),
                        Option(
                            "Cloud API (Mistral)",
                            id="cloud",
                        ),
                        id="choice-options",
                    )
                    yield NoMarkupStatic(
                        "[dim]↑↓ Navigate · Enter Select · Esc Cancel[/]",
                        id="choice-help",
                    )
            yield NoMarkupStatic("", classes="spacer")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option.id == "local":
            self.app.switch_screen("local_model")
        elif event.option.id == "cloud":
            self.app.switch_screen("api_key")
