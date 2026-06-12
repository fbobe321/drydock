"""Persistent configuration: ~/.drydock/config.toml.

Precedence, lowest to highest: built-in DEFAULTS → the config file → explicit
CLI flags. Missing keys are backfilled from DEFAULTS on load, and the merged
config is written back so the file self-heals to the current schema (the v2
lesson: users should never have to delete config.toml to pick up a new key).

Reading uses stdlib tomllib (3.11+). Writing is a tiny hand-rolled emitter for
our flat string/number/bool schema — no third-party dependency, in keeping with
the clean-room provenance rules.

All logic original to Drydock.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

# Only these keys live in the config file. Runtime-only values (cwd, the
# unlimited-tool-calls switch, etc.) are intentionally excluded.
DEFAULTS: dict[str, object] = {
    "model": "gemma4",
    "provider": "vllm",
    "base_url": "",
    "max_tokens": 4096,
    "temperature": 0.2,
    "theme": "harbor",
}


def default_config_path() -> Path:
    return Path.home() / ".drydock" / "config.toml"


def load_file(path: Path) -> dict:
    """Read a config.toml. Returns {} if missing or unparseable (never raises)."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    # string: escape backslash and quote, emit a basic TOML string
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dump_toml(data: dict) -> str:
    """Serialize a flat dict of str/number/bool to TOML text."""
    lines = ["# Drydock configuration — https://drydock.pages.dev", ""]
    for key in sorted(data):
        lines.append(f"{key} = {_toml_value(data[key])}")
    return "\n".join(lines) + "\n"


def save_file(data: dict, path: Path) -> bool:
    """Write the persistent keys of `data` to `path`. Returns success."""
    persistent = {k: data[k] for k in DEFAULTS if k in data}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_toml(persistent), encoding="utf-8")
        return True
    except OSError:
        return False


def merge(file_cfg: dict, cli_overrides: dict) -> dict:
    """Combine DEFAULTS < file < explicit CLI flags.

    `cli_overrides` should contain only flags the user actually passed
    (None values are ignored, so unset flags don't clobber the file).
    """
    cfg = dict(DEFAULTS)
    for key, val in file_cfg.items():
        if key in DEFAULTS:  # ignore unknown/stale keys in the file
            cfg[key] = val
    for key, val in cli_overrides.items():
        if val is not None:
            cfg[key] = val
    return cfg


def resolve(cli_overrides: dict, path: Path | None = None) -> dict:
    """Load the file, merge precedence, and return the runtime config.

    A NON-existent file is created once with pure DEFAULTS (no CLI flags — a
    transient --base-url for one run must not be baked in permanently). An
    EXISTING file is never modified: missing keys are backfilled only in the
    returned dict, never written back. This is deliberately conservative —
    the file may be shared or hand-edited, so we never reorder, reformat, or
    drop keys we don't recognize.
    """
    path = path or default_config_path()
    if not path.exists():
        save_file(dict(DEFAULTS), path)
        file_cfg = {}
    else:
        file_cfg = load_file(path)
    return merge(file_cfg, cli_overrides)
