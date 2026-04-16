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


def _detect_model_name(api_base: str) -> str | None:
    """Try to detect the model name from the vLLM/Ollama server.

    Returns the first model ID if the server responds, else None.
    """
    import urllib.request
    url = f"{api_base.rstrip('/')}/models"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            import json as _json
            data = _json.loads(resp.read())
            models = data.get("data", [])
            if models:
                return models[0].get("id")
    except Exception:
        pass
    return None


def _write_local_config(api_base: str, model_name: str) -> Path:
    """Merge a local-model provider into ~/.drydock/config.toml.

    PRESERVES existing config (read → merge → write) so defaults,
    existing providers, and user customisations survive. Adds the 'local'
    provider and model, sets active_model to point at it. If the config
    file doesn't exist yet, creates a full default one from VibeConfig.
    """
    config_dir = Path.home() / ".drydock"
    config_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = config_dir / "config.toml"

    # Load existing config (or start from defaults)
    existing: dict = {}
    if cfg_path.is_file():
        try:
            import tomli
            with cfg_path.open("rb") as f:
                existing = tomli.load(f)
        except Exception:
            pass

    # Ensure providers / models lists exist
    providers = existing.get("providers", [])
    if not isinstance(providers, list):
        providers = []
    models = existing.get("models", [])
    if not isinstance(models, list):
        models = []

    # Remove any previous 'local' entries (idempotent)
    providers = [p for p in providers if p.get("name") != "local"]
    models = [m for m in models if m.get("provider") != "local"]

    # Add the new local provider + model
    providers.append({
        "name": "local",
        "api_base": api_base,
        "api_key_env_var": "",
        "backend": "generic",
    })
    model_alias = model_name or _DEFAULT_MODEL
    models.append({
        "name": model_alias,
        "provider": "local",
        "alias": model_alias,
        "input_price": 0.0,
        "output_price": 0.0,
    })

    existing["providers"] = providers
    existing["models"] = models
    existing["active_model"] = model_alias

    with cfg_path.open("wb") as f:
        tomli_w.dump(existing, f)
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
        model_name = self.model_input.value.strip()
        if not model_name:
            detected = _detect_model_name(api_base)
            if detected:
                model_name = detected
            else:
                model_name = _DEFAULT_MODEL
        try:
            _write_local_config(api_base, model_name)
        except OSError as err:
            self.app.exit(f"save_error:{err}")
            return
        self.app.exit("completed")
