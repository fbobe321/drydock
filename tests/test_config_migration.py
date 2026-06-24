"""Upgrade / migration safety: an OLD config file (especially a v2 mistral-vibe
config, which is a completely different nested shape) must never break v3 — it
should load to a USABLE config, falling back to defaults for anything it can't
read. Regression guard for the v2->v3 upgrade path."""
from __future__ import annotations


from drydock import config as cfg

# A real v2 (mistral-vibe fork) config is nested: [[providers]] / [[models]] /
# active_model / [tools.*] — NONE of v3's flat top-level keys. v3 must ignore it
# wholesale and use defaults, not crash or inherit a stale/cloud endpoint.
V2_CONFIG = """
active_model = "local"
enable_telemetry = true
system_prompt_id = "ralph"
api_timeout = 600.0

[[providers]]
name = "vllm"
api_base = "http://localhost:8001/v1"
api_style = "openai"

[[models]]
name = "gemma4"
provider = "vllm"
alias = "local"
temperature = 0.2

[tools.bash]
permission = "always"
allowlist = ["echo", "ls"]
"""


def _load(tmp_path, text):
    p = tmp_path / "config.toml"
    if text is not None:
        p.write_text(text)
    file_cfg = cfg.load_file(p) if p.exists() else {}
    return cfg.merge(file_cfg, {})


def test_real_v2_config_loads_to_usable_defaults(tmp_path):
    m = _load(tmp_path, V2_CONFIG)
    # v2's nested keys are ignored; v3 falls back to its defaults
    assert m["model"] == "gemma4"
    assert m["base_url"]  # concrete, non-empty (so the endpoint is visible)
    assert m["provider"] == "vllm"


def test_malformed_toml_falls_back_to_defaults(tmp_path):
    m = _load(tmp_path, 'model = "gemma4\nbroken [[[ not toml')
    assert m["model"] == cfg.DEFAULTS["model"]
    assert m["base_url"] == cfg.DEFAULTS["base_url"]


def test_no_config_uses_defaults(tmp_path):
    m = _load(tmp_path, None)
    assert m["model"] == "gemma4"
    assert m["base_url"]  # not empty — the URL is visible/editable


def test_unknown_v2_keys_are_ignored(tmp_path):
    m = _load(tmp_path, 'enable_telemetry = true\napi_timeout = 600.0\nmodel = "gemma4"\n')
    assert "enable_telemetry" not in m
    assert "api_timeout" not in m
    assert m["model"] == "gemma4"


def test_partial_config_backfills_missing_keys(tmp_path):
    m = _load(tmp_path, 'model = "gemma4"\n')
    # everything else comes from DEFAULTS
    for k in cfg.DEFAULTS:
        assert k in m
    assert m["base_url"] == cfg.DEFAULTS["base_url"]


def test_explicit_user_values_are_respected(tmp_path):
    m = _load(tmp_path, 'base_url = "http://192.0.2.10:8000/v1"\nmodel = "gemma4"\n')
    assert m["base_url"] == "http://192.0.2.10:8000/v1"


def test_default_base_url_is_concrete_not_empty():
    # the fix: an empty default left configs with no visible endpoint
    assert cfg.DEFAULTS["base_url"], "base_url default must be a concrete URL"
    assert cfg.DEFAULTS["base_url"].startswith("http")


def test_v2_file_is_migrated_and_backed_up(tmp_path):
    """A real v2 file (no v3 keys) is backed up and replaced with a fresh,
    editable v3 config — the upgrade path that was invisible before."""
    p = tmp_path / "config.toml"
    p.write_text(V2_CONFIG)
    merged = cfg.resolve({}, path=p)
    # original preserved as a sibling .bak
    bak = p.with_name("config.toml.bak")
    assert bak.exists()
    assert "active_model" in bak.read_text()  # the v2 content is safe
    # the live file is now a clean v3 config with a visible base_url
    new_text = p.read_text()
    assert "base_url" in new_text
    assert "http" in new_text
    assert merged["base_url"] == cfg.DEFAULTS["base_url"]
    assert "active_model" not in new_text  # v2 junk gone from the live file


def test_real_v3_file_is_left_untouched(tmp_path):
    """A genuine v3 file (has a known key) must NOT be migrated/rewritten."""
    p = tmp_path / "config.toml"
    p.write_text('base_url = "http://192.0.2.20:8000/v1"\n# my hand edit\n')
    cfg.resolve({}, path=p)
    assert not p.with_name("config.toml.bak").exists()
    assert "# my hand edit" in p.read_text()  # untouched


def test_migration_does_not_clobber_existing_backup(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(V2_CONFIG)
    bak = p.with_name("config.toml.bak")
    bak.write_text("PRIOR BACKUP")
    cfg.resolve({}, path=p)
    assert bak.read_text() == "PRIOR BACKUP"  # not overwritten
