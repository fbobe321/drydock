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

import re
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
    # Proactive background-job completion notify: a command run (from the job's own
    # shell) when a background job finishes, with DRYDOCK_JOB_ID/_RC/_CMD in the env.
    # Point it at an operator hook (e.g. a Comms→Telegram push). Empty = no push
    # (the job still records its result; the agent reports it via the Jobs tool).
    "job_notify_cmd": "",
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
    # A custom system prompt injected into EVERY turn as the user's standing
    # instructions (unlike a per-project AGENTS.md, which is framed as optional
    # background). Two sources, file wins: the file ~/.drydock/system_prompt.md,
    # or this inline key. Empty = nothing added. Capped at 8000 chars.
    "system_prompt": "",
}


def default_config_path() -> Path:
    return Path.home() / ".drydock" / "config.toml"


def system_prompt_path() -> Path:
    """User's GLOBAL custom system-prompt file (applies to every project)."""
    return Path.home() / ".drydock" / "system_prompt.md"


def project_system_prompt_path(cwd: Path | None = None) -> Path:
    """A PER-PROJECT custom system-prompt file: system_prompt.md in the folder
    drydock was started from. Overrides the global one when present — different
    projects, different orders."""
    return (cwd or Path.cwd()) / "system_prompt.md"


# The template drydock drops so the file is always THERE and discoverable — the
# user never has to know the path or filename. It's ALL comments, so it has ZERO
# effect until the user writes real text below them (load_user_system_prompt
# strips <!-- --> blocks before use).
_SYSTEM_PROMPT_TEMPLATE = """\
<!--
  Drydock custom system prompt.

  Whatever you write in this file (OUTSIDE these comment blocks) is added to the
  system prompt on EVERY turn, in both the TUI and CLI — your standing instructions.
  Examples you might add below:
    - Always run the test suite after editing code.
    - Prefer the standard library; avoid adding new dependencies.
    - State your plan in one sentence before making changes.

  SCOPE: a system_prompt.md in a project's root folder is THAT PROJECT'S prompt;
  the copy at ~/.drydock/system_prompt.md applies to every project. A project
  file OVERRIDES the global one.

  This template has NO effect until you add your own text below this comment.
  Comment blocks like this one are ignored. Capped at 8000 characters.
  Restart drydock after editing for changes to take effect.
-->
"""


def _strip_comments(text: str) -> str:
    """Drop HTML comment blocks so the shipped template is inert until edited."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _read_prompt_file(p: Path) -> str:
    """Comment-stripped, whitespace-trimmed contents of a prompt file, or ''."""
    try:
        if p.exists():
            return _strip_comments(p.read_text()).strip()
    except OSError:
        pass
    return ""


def _write_template(p: Path) -> None:
    """Write the commented template to p if absent. Never overwrites; never raises."""
    try:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_SYSTEM_PROMPT_TEMPLATE, encoding="utf-8")
    except OSError:
        pass


def ensure_system_prompt_file(config_dir: Path | None = None) -> None:
    """Create the GLOBAL <config_dir>/system_prompt.md template if absent (config_dir
    defaults to ~/.drydock), so the feature is discoverable. Never overwrites."""
    _write_template((config_dir / "system_prompt.md") if config_dir is not None else system_prompt_path())


def ensure_project_system_prompt(cwd: Path | None = None) -> None:
    """Create system_prompt.md in the folder drydock was started from if absent, so
    it's right there in your project after the first launch — no git repo required.
    The ONE exception is the home directory itself: we don't drop a stray
    ~/system_prompt.md there (the global prompt already lives in ~/.drydock/).
    Never overwrites an existing file; never raises."""
    cwd = cwd or Path.cwd()
    try:
        if cwd.resolve() == Path.home().resolve():
            return
        _write_template(project_system_prompt_path(cwd))
    except OSError:
        pass


def load_user_system_prompt(config: dict | None = None, cwd: Path | None = None) -> str:
    """The user's custom system prompt, injected every turn as standing orders.

    Precedence (first with real content wins): the PER-PROJECT file
    <project>/.drydock/system_prompt.md, then the GLOBAL ~/.drydock/system_prompt.md,
    then the inline `system_prompt` config key. Comment blocks are stripped (so the
    shipped template is inert). Capped at 8000 chars. Returns '' when none has real
    content. Never raises.
    """
    text = _read_prompt_file(project_system_prompt_path(cwd)) or _read_prompt_file(system_prompt_path())
    if not text and config:
        text = str(config.get("system_prompt", "") or "").strip()
    if not text:
        return ""
    return (
        "\n\n## Custom instructions (from the user's configuration)\n\n"
        "The user has set these standing instructions; follow them throughout "
        "the session in addition to the guidance above.\n\n"
        f"{text[:8000]}"
    )


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
    # Drop the discoverable custom-system-prompt template alongside config.toml so
    # the user never has to know its path/name. Inert (all comments) until edited;
    # never overwrites an existing file.
    ensure_system_prompt_file(path.parent)
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
