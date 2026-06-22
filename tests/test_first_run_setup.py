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
