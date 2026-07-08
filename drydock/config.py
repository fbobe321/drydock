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
    # Concrete (not "") so a freshly-written config SHOWS the endpoint and the
    # user can edit it / point at another box. Empty fell back to the provider
    # default invisibly, which left base_url absent from the file. For a non-vllm
    # provider, override base_url (or use --base-url / the first-run prompt).
    "base_url": "http://localhost:8000/v1",
    "max_tokens": 8192,  # 4096 truncated large file writes mid-JSON (→ _raw fail)
    "temperature": 0.2,
    # The model server's context window (llama.cpp -c / vLLM --max-model-len).
    # Drives the ctx gauge AND when compaction fires (it compacts at ~60% of
    # this). It MUST match your server: if it's too high, drydock overflows the
    # real window BEFORE compacting (the server 400s) — set this to your -c
    # value. Default 65536 matches the bundled gemma4 server (-c 65536).
    "context_limit": 65536,
    # Auto-retry a hung model call: if the server produces NOTHING for this many
    # seconds, drydock abandons the wedged request and re-issues it (a fresh
    # generation usually isn't stalled — a known gemma/llama.cpp hang). 0 = off.
    # Set to e.g. 600 on a stall-prone local server. Bounded to a few retries.
    "stall_retry_secs": 0,
    "theme": "harbor",
    # Optional SECOND model ("advisor") for a stronger second opinion — e.g. a
    # Gemini OpenAI-compatible endpoint on another box. Empty = disabled. The
    # agent can call the `Consult` tool and you can use `/ask`; configure with
    # `/advisor`. It's just another OpenAI-compatible endpoint (no extra deps).
    "advisor_model": "",
    "advisor_base_url": "",
    "advisor_api_key": "",
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
    EXISTING v3 file is never modified: missing keys are backfilled only in the
    returned dict, never written back. This is deliberately conservative — the
    file may be shared or hand-edited, so we never reorder, reformat, or drop
    keys we don't recognize.

    A LEGACY/foreign file (a v2 config, or a malformed one) is migrated: backed
    up to <name>.bak once, then replaced with a fresh DEFAULTS file so the user
    has an editable v3 config with a visible endpoint.
    """
    path = path or default_config_path()
    if not path.exists():
        save_file(dict(DEFAULTS), path)
        file_cfg = {}
    else:
        file_cfg = load_file(path)
        # Legacy/foreign-config migration. A file with NONE of our keys is either
        # a v2 mistral-vibe config (nested [[providers]]/[[models]]/active_model)
        # or malformed/empty — in every case the user can't edit it to set
        # base_url, model, etc., and v2's nested tables can't round-trip through
        # our flat emitter. Back it up once and write a fresh, complete v3 file so
        # the endpoint is visible and editable (the v2->v3 upgrade path). A real
        # v3 file (>=1 recognized key) is left untouched — we never reformat it or
        # drop keys we don't recognize.
        if file_cfg and not any(k in DEFAULTS for k in file_cfg):
            backup = path.with_name(path.name + ".bak")
            try:
                if not backup.exists():
                    backup.write_bytes(path.read_bytes())
            except OSError:
                pass
            save_file(dict(DEFAULTS), path)
            file_cfg = {}
    return merge(file_cfg, cli_overrides)
