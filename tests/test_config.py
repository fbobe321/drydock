"""Tests for persistent configuration (defaults < file < CLI flags)."""
from __future__ import annotations

import tomllib

from drydock import config as cfg


def test_defaults_when_no_file_and_no_flags(tmp_path):
    out = cfg.resolve({}, tmp_path / "config.toml")
    assert out["model"] == "gemma4"
    assert out["provider"] == "vllm"
    assert out["temperature"] == 0.2


def test_file_overrides_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "qwen"\ntemperature = 0.7\n')
    out = cfg.resolve({}, path)
    assert out["model"] == "qwen"
    assert out["temperature"] == 0.7
    assert out["provider"] == "vllm"  # still default


def test_cli_overrides_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "qwen"\n')
    out = cfg.resolve({"model": "gemma4", "provider": None}, path)
    assert out["model"] == "gemma4"      # CLI wins
    assert out["provider"] == "vllm"     # None CLI flag ignored → default


def test_none_cli_flags_do_not_clobber_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('base_url = "http://localhost:9000/v1"\n')
    out = cfg.resolve({"base_url": None}, path)
    assert out["base_url"] == "http://localhost:9000/v1"


def test_existing_file_is_never_modified(tmp_path):
    # A partial / hand-edited file must be left exactly as-is on disk; missing
    # keys are backfilled only in the returned config, not written back. This
    # is what stops v3 clobbering a config shared with another tool.
    path = tmp_path / "config.toml"
    original = 'model = "qwen"\nunknown_key = 42\n'
    path.write_text(original)
    out = cfg.resolve({}, path)
    assert path.read_text() == original          # file untouched
    assert out["model"] == "qwen"
    assert out["provider"] == "vllm"             # backfilled in memory only


def test_first_run_creates_file_with_defaults_only(tmp_path):
    # First run writes a clean baseline — CLI flags apply to the run but are
    # NOT persisted (a transient --base-url must not be baked in).
    path = tmp_path / "sub" / "config.toml"
    assert not path.exists()
    out = cfg.resolve({"base_url": "http://localhost:9/v1"}, path)
    assert path.exists()
    assert out["base_url"] == "http://localhost:9/v1"  # used this run
    with path.open("rb") as f:
        written = tomllib.load(f)
    assert written["base_url"] == ""             # but NOT persisted


def test_unparseable_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is not valid toml = = =\n")
    out = cfg.resolve({}, path)
    assert out["model"] == "gemma4"  # no crash, defaults used


def test_dump_toml_roundtrips_types(tmp_path):
    data = {"model": "g", "max_tokens": 4096, "temperature": 0.2, "theme": "harbor"}
    text = cfg.dump_toml(data)
    parsed = tomllib.loads(text)
    assert parsed["max_tokens"] == 4096
    assert parsed["temperature"] == 0.2
    assert parsed["model"] == "g"


def test_save_drops_runtime_only_keys(tmp_path):
    path = tmp_path / "config.toml"
    cfg.save_file({"model": "g", "cwd": "/tmp", "context_limit": 999}, path)
    with path.open("rb") as f:
        written = tomllib.load(f)
    assert "cwd" not in written and "context_limit" not in written
    assert written["model"] == "g"
