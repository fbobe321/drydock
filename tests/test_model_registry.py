"""Tests for the model registry (multiple models in config.toml, configurable
default, /model routes to the right endpoint)."""
from __future__ import annotations

from drydock import config as cfgmod


def test_toml_roundtrips_a_models_list(tmp_path):
    # The emitter must round-trip a list of inline tables through tomllib.
    cfg = dict(cfgmod.DEFAULTS)
    cfgmod.upsert_model(cfg, "gemma4", "http://localhost:8000/v1", "vllm")
    cfgmod.upsert_model(cfg, "qwen", "http://localhost:8001/v1", "vllm")
    cfg["default_model"] = "qwen"
    path = tmp_path / "config.toml"
    assert cfgmod.save_file(cfg, path)
    on_disk = cfgmod.load_file(path)
    assert len(on_disk["models"]) == 2
    assert cfgmod.find_model(on_disk, "qwen")["base_url"] == "http://localhost:8001/v1"
    assert on_disk["default_model"] == "qwen"


def test_upsert_updates_existing():
    cfg = dict(cfgmod.DEFAULTS)
    cfgmod.upsert_model(cfg, "a", "http://h1/v1")
    cfgmod.upsert_model(cfg, "a", "http://h2/v1", "ollama")
    assert len(cfgmod.list_models(cfg)) == 1
    assert cfgmod.find_model(cfg, "a")["base_url"] == "http://h2/v1"
    assert cfgmod.find_model(cfg, "a")["provider"] == "ollama"


def test_apply_model_routes_to_its_endpoint():
    # The bug fix: switching to a registered model changes the endpoint too.
    cfg = dict(cfgmod.DEFAULTS)
    cfg["base_url"] = "http://localhost:8000/v1"
    cfgmod.upsert_model(cfg, "qwen", "http://localhost:8001/v1", "vllm")
    assert cfgmod.apply_model(cfg, "qwen") is True
    assert cfg["model"] == "qwen"
    assert cfg["base_url"] == "http://localhost:8001/v1"  # traffic now goes here


def test_apply_unregistered_model_just_sets_name():
    cfg = dict(cfgmod.DEFAULTS)
    cfg["base_url"] = "http://localhost:8000/v1"
    assert cfgmod.apply_model(cfg, "mystery") is False
    assert cfg["model"] == "mystery"
    assert cfg["base_url"] == "http://localhost:8000/v1"  # unchanged


def test_default_model_applied_at_launch():
    cfg = dict(cfgmod.DEFAULTS)
    cfgmod.upsert_model(cfg, "a", "http://a/v1")
    cfgmod.upsert_model(cfg, "b", "http://b/v1", "ollama")
    cfg["default_model"] = "b"
    cfgmod.resolve_active_model(cfg)
    assert cfg["model"] == "b"
    assert cfg["base_url"] == "http://b/v1"
    assert cfg["provider"] == "ollama"


def test_remove_model_clears_default():
    cfg = dict(cfgmod.DEFAULTS)
    cfgmod.upsert_model(cfg, "a", "http://a/v1")
    cfg["default_model"] = "a"
    assert cfgmod.remove_model(cfg, "a") is True
    assert cfgmod.list_models(cfg) == []
    assert cfg["default_model"] == ""


def test_save_file_persists_registry_keys(tmp_path):
    cfg = dict(cfgmod.DEFAULTS)
    cfgmod.upsert_model(cfg, "a", "http://a/v1")
    path = tmp_path / "c.toml"
    cfgmod.save_file(cfg, path)
    reloaded = cfgmod.load_file(path)
    assert "models" in reloaded and reloaded["models"][0]["name"] == "a"
