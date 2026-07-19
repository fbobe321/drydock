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
    # Inject bundled technique recipes (drydock/recipes.py) relevant to the task
    # into the system prompt, so a local model has the *method* a task needs
    # instead of guessing. Retrieval is keyword-overlap; only relevant recipes are
    # added (none = nothing injected). Set false to disable.
    "recipes": True,
    # Keep the structured objective + acceptance criteria (task_state.py) in the
    # system prompt every turn so they survive context compaction — the model can't
    # drift off the original goal on a long task. Set false to disable.
    "task_anchor": True,
    # Verification gate (PRD Epic B): if the model claims "done" after changing
    # files but never ran a test/check/its own code, nudge it to verify first
    # instead of accepting completion. Bounded. Set false to disable.
    "verify_gate": True,
    # Durable per-session execution trace (events.py) written to ~/.drydock/events/
    # — inspect/diagnose/replay a task. Set false to disable.
    "event_log": True,
    # Event-store backend: "jsonl" (one file, human-readable) or "sqlite" (indexed,
    # queryable by seq/type — better for long/large traces). Both are append-only.
    "event_store": "jsonl",
    # Durable per-turn session snapshot so an interrupted task can be continued
    # with /resume (writes to ~/.drydock/resume/). Set false to disable.
    "resume": True,
    # Recovery tuning (PRD Epic K / §13). no_progress_window: how many actions of
    # no progress before recovery escalates; suppression_iterations: how long a
    # looping action stays suppressed; max_recovery_attempts: recovery interventions
    # before a controlled stop (0 = unlimited).
    "recovery_no_progress_window": 5,
    "recovery_suppression_iterations": 2,
    "max_recovery_attempts": 0,
    # An exact (call, result) pair recurring this many times — consecutively or
    # not — is treated as cycling and that signature is suppressed (stage 3),
    # even when interleaved actions look productive. Polling is exempt (its
    # result changes each time).
    "recovery_same_outcome_threshold": 6,
    # Time-aware effort governor: once any single LLM turn takes longer than this
    # many seconds, the rest of the request runs decisive (low reasoning effort,
    # tight token cap) to protect the time budget — a think-bound task otherwise
    # burns a whole window on a handful of giant reasoning turns. 0 = off.
    "turn_seconds_soft_cap": 240,
    # Verified-trajectory export (RSI training-data collection). When set to a path,
    # drydock writes the full task trajectory (system prompt + transcript) there at
    # task end; a benchmark harness keeps only verifier-passing ones. Empty = off.
    "trajectory_file": "",
    # Tool names dynamic tool selection must NEVER trim (tool_select.py), on top
    # of the built-in core coding set — e.g. ["WebSearch", "WebFetch"] keeps the
    # web tools surfaced even on tasks whose text never mentions the web. Names
    # not in the registry are ignored.
    "pin_tools": [],
    # URL substrings the web tools refuse: WebSearch drops matching results,
    # WebFetch declines matching URLs (with a plain message, never an error).
    # Used to keep benchmark/solution sites out of harvested training runs.
    "web_denylist": [],
    "theme": "harbor",
    # Optional SECOND model ("advisor") for a stronger second opinion — e.g. a
    # Gemini OpenAI-compatible endpoint on another box. Empty = disabled. The
    # agent can call the `Consult` tool and you can use `/ask`; configure with
    # `/advisor`. It's just another OpenAI-compatible endpoint (no extra deps).
    "advisor_model": "",
    "advisor_base_url": "",
    "advisor_api_key": "",
    # Registry of known models so you can keep several servers configured and
    # switch between them with `/model <name>` — each entry routes to its OWN
    # endpoint (this is why switching by name alone wasn't enough). A list of
    # {name, base_url, provider}. `default_model` names the one used at launch
    # (falls back to `model`). Manage with `/model add|default|remove`.
    "models": [],
    "default_model": "",
}


def default_config_path() -> Path:
    return Path.home() / ".drydock" / "config.toml"


# ── Model registry ────────────────────────────────────────────────────────
# A model entry is {"name", "base_url", "provider"}. Kept in cfg["models"];
# cfg["default_model"] names the launch default. These helpers keep the list
# clean and let `/model <name>` route to the RIGHT endpoint.

def list_models(cfg: dict) -> list[dict]:
    return [m for m in (cfg.get("models") or []) if isinstance(m, dict) and m.get("name")]


def find_model(cfg: dict, name: str) -> dict | None:
    for m in list_models(cfg):
        if m.get("name") == name:
            return m
    return None


def upsert_model(cfg: dict, name: str, base_url: str, provider: str = "vllm") -> None:
    """Add or update a registered model (by name). Persist with save_file after."""
    entry = {"name": name, "base_url": base_url, "provider": provider or "vllm"}
    models = list_models(cfg)
    for i, m in enumerate(models):
        if m.get("name") == name:
            models[i] = entry
            break
    else:
        models.append(entry)
    cfg["models"] = models


def remove_model(cfg: dict, name: str) -> bool:
    models = list_models(cfg)
    kept = [m for m in models if m.get("name") != name]
    cfg["models"] = kept
    if cfg.get("default_model") == name:
        cfg["default_model"] = ""
    return len(kept) != len(models)


def apply_model(cfg: dict, name: str) -> bool:
    """Switch the active model to `name`. If it's a REGISTERED model, also route
    to its endpoint + provider (the fix for 'switched name but traffic went to the
    first server'). Returns True if it matched a registered entry."""
    cfg["model"] = name
    entry = find_model(cfg, name)
    if entry:
        cfg["base_url"] = entry.get("base_url") or cfg.get("base_url")
        cfg["provider"] = entry.get("provider") or cfg.get("provider")
        return True
    return False


def resolve_active_model(cfg: dict) -> dict:
    """At launch, if the config names a default_model (or the current `model`
    matches a registered entry), apply its endpoint so traffic routes correctly.
    Mutates and returns cfg."""
    name = cfg.get("default_model") or cfg.get("model")
    if name and find_model(cfg, name):
        apply_model(cfg, name)
    return cfg


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
    if isinstance(value, dict):
        # inline table: {k = v, ...} — used for a models-registry entry.
        inner = ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items())
        return "{" + inner + "}"
    if isinstance(value, (list, tuple)):
        # array (of inline tables or scalars) — used for the models list.
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
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
        # `models` is EXCLUDED from the v3 markers: v2 also had a [[models]] array,
        # so a file whose only recognized key is `models` is still a v2 file to be
        # migrated (not a v3 file to preserve).
        _v3_markers = set(DEFAULTS) - {"models"}
        if file_cfg and not any(k in _v3_markers for k in file_cfg):
            backup = path.with_name(path.name + ".bak")
            try:
                if not backup.exists():
                    backup.write_bytes(path.read_bytes())
            except OSError:
                pass
            save_file(dict(DEFAULTS), path)
            file_cfg = {}
    return merge(file_cfg, cli_overrides)
