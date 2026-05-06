"""Regression tests for config upgrade migration (issue #17).

Existing user configs created by older drydock versions don't get
top-level keys added in subsequent releases — `bootstrap_config_files`
only writes the file when it doesn't exist. `DrydockConfig._migrate()`
backfills any missing top-level keys while preserving user values
and never touching nested sections (providers/models/tools/etc).
"""
from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path

import pytest
import tomli_w


def _setup_config(tmpdir: Path, contents: dict) -> Path:
    drydock_dir = tmpdir / ".drydock"
    drydock_dir.mkdir(parents=True, exist_ok=True)
    cfg = drydock_dir / "config.toml"
    with cfg.open("wb") as f:
        tomli_w.dump(contents, f)
    return cfg


def _migrate_in_subprocess(drydock_home: Path) -> dict:
    """Run the migration in a subprocess (clean env so module-level
    DRYDOCK_HOME resolves to our temp dir) and return the resulting
    config dict."""
    import subprocess
    py = "/home/bobef/miniforge3/envs/drydock/bin/python3"
    code = (
        "from drydock.core.config.harness_files._harness_manager "
        "import init_harness_files_manager\n"
        "init_harness_files_manager('user')\n"
        "from drydock.core.config._settings import DrydockConfig\n"
        "DrydockConfig._migrate()\n"
    )
    env = {**os.environ, "DRYDOCK_HOME": str(drydock_home)}
    subprocess.run([py, "-c", code], env=env, check=True, capture_output=True)
    cfg_file = drydock_home / "config.toml"
    with cfg_file.open("rb") as f:
        return tomllib.load(f)


def test_migration_adds_missing_top_level_keys():
    """An older config missing slim_system_prompt should get it back."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup_config(tmp, {
            "active_model": "local",
            "auto_approve": False,
            "system_prompt_id": "cli",
            "providers": [{"name": "mistral", "api_base": "x",
                           "api_key_env_var": "MISTRAL_API_KEY",
                           "backend": "mistral"}],
        })
        cfg = _migrate_in_subprocess(tmp / ".drydock")
        # Missing top-level keys should now be present.
        assert "slim_system_prompt" in cfg
        assert "auto_compact_threshold" in cfg
        assert "context_warnings" in cfg
        # User values preserved verbatim.
        assert cfg["active_model"] == "local"
        assert cfg["system_prompt_id"] == "cli"
        # Nested sections not touched.
        assert cfg["providers"] == [{"name": "mistral", "api_base": "x",
                                     "api_key_env_var": "MISTRAL_API_KEY",
                                     "backend": "mistral"}]


def test_migration_idempotent():
    """Running _migrate() twice should produce the same result."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup_config(tmp, {
            "active_model": "local",
            "providers": [],
        })
        first = _migrate_in_subprocess(tmp / ".drydock")
        second = _migrate_in_subprocess(tmp / ".drydock")
        assert first == second


def test_migration_skips_heavyweight_section_keys():
    """User-customizable section keys (providers/models/tools) must
    not be backfilled — leaving them missing means user explicitly
    didn't want defaults there."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup_config(tmp, {
            "active_model": "local",
            "providers": [{"name": "only", "api_base": "x",
                           "api_key_env_var": "", "backend": "generic"}],
        })
        cfg = _migrate_in_subprocess(tmp / ".drydock")
        # Only this user's one provider — no default mistral/llamacpp injected.
        assert len(cfg["providers"]) == 1
        # models / tools / session_logging not added.
        assert "models" not in cfg
        assert "tools" not in cfg
        assert "session_logging" not in cfg


def test_migration_no_op_when_no_config_file():
    """Calling _migrate() when there's no existing config must be safe."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / ".drydock").mkdir(parents=True, exist_ok=True)
        # Don't create config.toml.
        # Migrate should silently no-op and not raise.
        import subprocess
        py = "/home/bobef/miniforge3/envs/drydock/bin/python3"
        code = (
            "from drydock.core.config.harness_files._harness_manager "
            "import init_harness_files_manager\n"
            "init_harness_files_manager('user')\n"
            "from drydock.core.config._settings import DrydockConfig\n"
            "DrydockConfig._migrate()\n"
        )
        env = {**os.environ, "DRYDOCK_HOME": str(tmp / ".drydock")}
        result = subprocess.run([py, "-c", code], env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        # Migrate should NOT have created the file.
        assert not (tmp / ".drydock" / "config.toml").exists()
