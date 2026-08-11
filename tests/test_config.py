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
    path.write_text('model = "mistral"\ntemperature = 0.7\n')
    out = cfg.resolve({}, path)
    assert out["model"] == "mistral"
    assert out["temperature"] == 0.7
    assert out["provider"] == "vllm"  # still default


def test_cli_overrides_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "mistral"\n')
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
    original = 'model = "mistral"\nunknown_key = 42\n'
    path.write_text(original)
    out = cfg.resolve({}, path)
    assert path.read_text() == original          # file untouched
    assert out["model"] == "mistral"
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
    assert written["base_url"] == "http://localhost:8000/v1"  # the DEFAULT, NOT the CLI override


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
    # cwd is runtime-only (dropped); context_limit is a real persistent setting
    # (users must be able to match it to their server's window).
    assert "cwd" not in written
    assert written["context_limit"] == 999
    assert written["model"] == "g"


# ── custom system prompt (~/.drydock/system_prompt.md or the `system_prompt` key) ──

def test_user_system_prompt_empty_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: tmp_path))
    assert cfg.load_user_system_prompt({}) == ""
    assert cfg.load_user_system_prompt({"system_prompt": ""}) == ""


def test_user_system_prompt_inline_key(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: tmp_path))
    out = cfg.load_user_system_prompt({"system_prompt": "Always answer in French."})
    assert "Always answer in French." in out
    assert "Custom instructions" in out


def test_user_system_prompt_file_wins_over_inline(monkeypatch, tmp_path):
    d = tmp_path / ".drydock"
    d.mkdir()
    (d / "system_prompt.md").write_text("FILE WINS")
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: tmp_path))
    out = cfg.load_user_system_prompt({"system_prompt": "inline loses"})
    assert "FILE WINS" in out
    assert "inline loses" not in out


def test_user_system_prompt_capped_at_8000(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: tmp_path))
    out = cfg.load_user_system_prompt({"system_prompt": "x" * 20000})
    assert out.count("x") == 8000


def test_system_prompt_key_in_defaults():
    assert "system_prompt" in cfg.DEFAULTS
    assert cfg.DEFAULTS["system_prompt"] == ""


def test_resolve_creates_inert_system_prompt_template(tmp_path):
    # resolve() drops the template next to config.toml, and it has NO effect until edited
    cfgpath = tmp_path / "config.toml"
    cfg.resolve({}, cfgpath)
    tmpl = tmp_path / "system_prompt.md"
    assert tmpl.exists(), "template should be auto-created"
    assert "<!--" in tmpl.read_text()
    # inert: all-comments template contributes nothing to the prompt
    assert cfg._strip_comments(tmpl.read_text()).strip() == ""


def test_ensure_system_prompt_never_overwrites(tmp_path):
    f = tmp_path / "system_prompt.md"
    f.write_text("MY REAL PROMPT")
    cfg.ensure_system_prompt_file(tmp_path)
    assert f.read_text() == "MY REAL PROMPT"


def test_load_strips_comments_keeps_real_text(monkeypatch, tmp_path):
    d = tmp_path / ".drydock"; d.mkdir()
    (d / "system_prompt.md").write_text("<!-- ignore me -->\nReal instruction here.")
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: tmp_path))
    out = cfg.load_user_system_prompt({})
    assert "Real instruction here." in out
    assert "ignore me" not in out


def test_load_ignores_comment_only_file(monkeypatch, tmp_path):
    d = tmp_path / ".drydock"; d.mkdir()
    (d / "system_prompt.md").write_text(cfg._SYSTEM_PROMPT_TEMPLATE)
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: tmp_path))
    assert cfg.load_user_system_prompt({}) == ""


# ── per-project system prompt (<project>/.drydock/system_prompt.md) ──

def test_project_prompt_overrides_global(monkeypatch, tmp_path):
    home = tmp_path / "home"; (home / ".drydock").mkdir(parents=True)
    (home / ".drydock" / "system_prompt.md").write_text("GLOBAL RULES")
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: home))
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / "system_prompt.md").write_text("PROJECT RULES")
    out = cfg.load_user_system_prompt({}, cwd=proj)
    assert "PROJECT RULES" in out
    assert "GLOBAL RULES" not in out


def test_global_used_when_no_project_file(monkeypatch, tmp_path):
    home = tmp_path / "home"; (home / ".drydock").mkdir(parents=True)
    (home / ".drydock" / "system_prompt.md").write_text("GLOBAL RULES")
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: home))
    proj = tmp_path / "proj"; proj.mkdir()  # no project system_prompt.md
    out = cfg.load_user_system_prompt({}, cwd=proj)
    assert "GLOBAL RULES" in out


def test_ensure_project_prompt_creates_in_any_dir(tmp_path):
    # any launch folder (git or not) gets the template at its root
    plain = tmp_path / "proj"; plain.mkdir()
    cfg.ensure_project_system_prompt(plain)
    assert (plain / "system_prompt.md").exists()


def test_ensure_project_prompt_skips_home(monkeypatch, tmp_path):
    # never drop a stray ~/system_prompt.md in the home root
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: tmp_path))
    cfg.ensure_project_system_prompt(tmp_path)
    assert not (tmp_path / "system_prompt.md").exists()


def test_ensure_project_prompt_never_overwrites(tmp_path):
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True)
    (repo / "system_prompt.md").write_text("MINE")
    cfg.ensure_project_system_prompt(repo)
    assert (repo / "system_prompt.md").read_text() == "MINE"
