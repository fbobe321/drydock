"""Local-LLM setup screen — alternative to the Mistral API key path.

Asks the user for an api_base URL and a model name, writes a minimal
config.toml pointing at it, and exits onboarding marking it complete.
The user can refine later via `/setup-model` or by editing config.toml
directly. This is the "I'll run my own vLLM/Ollama/LM Studio" path
described in github issue #3.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import tomli_w
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, Vertical
from textual.validation import Length, Regex
from textual.widgets import Input

from drydock.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from drydock.setup.onboarding.base import OnboardingScreen


_DEFAULT_API_BASE = "http://localhost:8000/v1"
_DEFAULT_MODEL = "local"


def _write_local_config(api_base: str, model_name: str) -> Path:
    """Write a minimal local-model config to ~/.drydock/config.toml.

    Adds a 'local' provider pointing at api_base, a 'local' model alias
    pointing at the user's model name, and sets it as the active model.
    Doesn't touch the existing Mistral defaults — the user can still
    add a Mistral key later if they want.
    """
    config_dir = Path.home() / ".drydock"
    config_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = config_dir / "config.toml"
    payload = {
        "active_model": model_name or _DEFAULT_MODEL,
        "providers": [
            {
                "name": "local",
                "api_base": api_base,
                "api_key_env_var": "",
                "backend": "generic",
            },
        ],
        "models": [
            {
                "name": model_name or _DEFAULT_MODEL,
                "provider": "local",
                "alias": model_name or _DEFAULT_MODEL,
                "input_price": 0.0,
                "output_price": 0.0,
            },
        ],
    }
    with cfg_path.open("wb") as f:
        tomli_w.dump(payload, f)
    return cfg_path


class LocalModelScreen(OnboardingScreen):
    """Step-2 alternative: configure a local OpenAI-compatible endpoint."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = None

    def compose(self) -> ComposeResult:
        self.api_base_input = Input(
            value=_DEFAULT_API_BASE,
            id="api-base",
            placeholder=_DEFAULT_API_BASE,
            validators=[
                Length(minimum=1, failure_description="API base URL required."),
                Regex(
                    r"^https?://.+",
                    failure_description="Must start with http:// or https://",
                ),
            ],
        )
        self.model_input = Input(
            value="",
            id="model-name",
            placeholder="model name (e.g. gemma4, llama-3-8b)",
            validators=[Length(minimum=1, failure_description="Model name required.")],
        )
        with Vertical(id="local-model-outer"):
            yield NoMarkupStatic("", classes="spacer")
            yield Center(NoMarkupStatic(
                "Local LLM setup",
                id="local-model-title",
            ))
            with Center():
                with Vertical(id="local-model-content"):
                    yield NoMarkupStatic(
                        "Point Drydock at any OpenAI-compatible endpoint "
                        "(vLLM, Ollama, LM Studio, llama.cpp, etc.):",
                        id="local-model-hint",
                    )
                    yield NoMarkupStatic("API base URL:")
                    yield Center(Horizontal(self.api_base_input, id="api-base-row"))
                    yield NoMarkupStatic("Model name (as your server reports it):")
                    yield Center(Horizontal(self.model_input, id="model-row"))
                    yield NoMarkupStatic("", id="feedback")
                    yield NoMarkupStatic(
                        "[dim]Tab to switch fields · Enter on the model "
                        "field to save · Esc to skip[/]",
                        id="local-model-help",
                    )
            yield NoMarkupStatic("", classes="spacer")

    def on_mount(self) -> None:
        self.api_base_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter on api-base → jump to model field; Enter on model → save.
        if event.input.id == "api-base":
            self.model_input.focus()
            return
        if event.input.id == "model-name":
            self._save_and_finish()

    def _save_and_finish(self) -> None:
        api_base = self.api_base_input.value.strip() or _DEFAULT_API_BASE
        model_name = self.model_input.value.strip() or _DEFAULT_MODEL
        try:
            _write_local_config(api_base, model_name)
        except OSError as err:
            self.app.exit(f"save_error:{err}")
            return
        self.app.exit("completed")
