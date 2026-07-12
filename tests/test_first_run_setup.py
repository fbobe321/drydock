"""First-launch setup: prompt for model server URL + name, persist to config."""
from __future__ import annotations

from drydock import cli
from drydock import config as cfgmod


def test_first_run_setup_persists_url_and_model(tmp_path, monkeypatch):
    # No server running → defaults; user types a custom URL + model.
    monkeypatch.setattr(cli, "_first_run_setup", cli._first_run_setup)  # ensure present
    import drydock.detect as detect
    monkeypatch.setattr(detect, "detect_local_llms", lambda: [])
    answers = iter(["http://127.0.0.1:8000/v1", "gemma4"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))

    cfg_path = tmp_path / "config.toml"
    cfg, onboarding = cli._first_run_setup(dict(cfgmod.DEFAULTS), cfg_path, cfgmod)

    assert cfg["base_url"] == "http://127.0.0.1:8000/v1"
    assert cfg["model"] == "gemma4"
    # Persisted to disk.
    on_disk = cfgmod.load_file(cfg_path)
    assert on_disk["base_url"] == "http://127.0.0.1:8000/v1"
    assert on_disk["model"] == "gemma4"
    assert "gemma4" in onboarding and "127.0.0.1:8000" in onboarding


def test_first_run_setup_uses_defaults_on_empty_input(tmp_path, monkeypatch):
    import drydock.detect as detect
    monkeypatch.setattr(detect, "detect_local_llms", lambda: [])
    monkeypatch.setattr("builtins.input", lambda *_: "")  # user just hits Enter
    cfg, _ = cli._first_run_setup(dict(cfgmod.DEFAULTS), tmp_path / "c.toml", cfgmod)
    assert cfg["base_url"] == "http://127.0.0.1:8000/v1"  # the documented default
    assert cfg["model"] == "gemma4"


class _Args:
    def __init__(self, model=None): self.model = model


def test_autowire_uses_detected_server_no_prompt(tmp_path, monkeypatch):
    """First launch with a live server: wire it up automatically, NO input()."""
    from drydock import cli, config as cfgmod, detect
    monkeypatch.setattr(detect, "detect_local_llms",
                        lambda: [{"provider": "ollama", "base_url": "http://localhost:11434/v1",
                                  "models": ["llama3"]}])
    # input() must NOT be called on the auto-wire path
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("prompted!")))
    cfg_path = tmp_path / "c.toml"
    cfg, onboarding = cli._resolve_first_run(dict(cfgmod.DEFAULTS), _Args(), cfg_path, cfgmod, interactive=True)
    assert cfg["base_url"] == "http://localhost:11434/v1"
    assert cfg["provider"] == "ollama" and cfg["model"] == "llama3"
    assert "Detected" in onboarding and cfg_path.exists()   # persisted


def test_autowire_respects_explicit_model(tmp_path, monkeypatch):
    from drydock import cli, config as cfgmod, detect
    monkeypatch.setattr(detect, "detect_local_llms",
                        lambda: [{"provider": "vllm", "base_url": "http://localhost:8000/v1",
                                  "models": ["served-model"]}])
    cfg, _ = cli._resolve_first_run(dict(cfgmod.DEFAULTS), _Args(model="gemma4"),
                                    tmp_path / "c.toml", cfgmod, interactive=True)
    assert cfg["model"] == "gemma4"   # --model wins over the detected id


def test_nothing_detected_noninteractive_shows_help(tmp_path, monkeypatch):
    from drydock import cli, config as cfgmod, detect
    monkeypatch.setattr(detect, "detect_local_llms", lambda: [])
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("prompted!")))
    cfg, onboarding = cli._resolve_first_run(dict(cfgmod.DEFAULTS), _Args(), tmp_path / "c.toml",
                                             cfgmod, interactive=False)
    assert "No local LLM detected" in onboarding   # tells the user how to set a URL


def test_nothing_detected_interactive_prompts(tmp_path, monkeypatch):
    from drydock import cli, config as cfgmod, detect
    monkeypatch.setattr(detect, "detect_local_llms", lambda: [])
    monkeypatch.setattr("builtins.input", lambda *a: "http://myhost:9000/v1")
    cfg, _ = cli._resolve_first_run(dict(cfgmod.DEFAULTS), _Args(), tmp_path / "c.toml",
                                    cfgmod, interactive=True)
    assert cfg["base_url"] == "http://myhost:9000/v1"   # user's URL honored
