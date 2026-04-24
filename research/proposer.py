#!/usr/bin/env python3
"""Meta-Harness Opus-backed proposer.

Replaces random search with an LLM agent that reads source + recent
traces + results.tsv, picks ONE mutation, and returns a structured
proposal. The experimenter applies the proposal on top of the current
best config, writes a staged candidate, and invokes the kernel.

Transport: tries `anthropic` SDK first (ANTHROPIC_API_KEY env var),
falls back to `claude -p` CLI if the SDK isn't available. Same
two-layer pattern drydock/admiral/opus_escalator.py uses.

Scope (v1 — overnight build):
  - Mutates admiral knobs, harness thresholds, admiral detector
    thresholds, env flags. These are all TOML-surface mutations.
  - Does NOT mutate prompts yet (gemma4.md / cli.md). Path exists in
    config_base.toml with mutable=true; wiring added in a follow-up.
  - Does NOT apply patches to source files. Also follow-up.

Contract: returns either
  {
    "target": "knob" | "harness_threshold" | "admiral_detector"
              | "env_flag" | "prompt",
    "name": "<field-name>",
    "value": <numeric or string; for target=prompt, the FULL new
             prompt text as a string>,
    "reason": "<≤200 char human-readable>",
  }
or None on any failure — experimenter then falls through to random.

For target="prompt", `name` must match a [prompts.<name>] entry with
mutable=true in config_base.toml (currently "gemma4" or "cli"). The
value is the complete replacement prompt. The kernel writes it to
the candidate's isolated prompts dir and the TUI picks it up via
DRYDOCK_PROMPTS_DIR.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parent
DRYDOCK_ROOT = RESEARCH_DIR.parent  # /data3/drydock — repo root
OPUS_MODEL = "claude-opus-4-7"
OPUS_TIMEOUT_SEC = 120
# Local proposer endpoint. Defaults to the same balancer drydock uses
# so the proposer is air-gap-compatible — the whole pitch collapses
# if the self-tuning loop phones home.
LOCAL_ENDPOINT = os.environ.get("DRYDOCK_RESEARCH_PROPOSER_URL",
                                "http://localhost:8001/v1")
LOCAL_MODEL = os.environ.get("DRYDOCK_RESEARCH_PROPOSER_MODEL", "gemma4")
LOCAL_TIMEOUT_SEC = 180            # Gemma 4 thinks longer than Opus
MAX_TRACES_EACH_SIDE = 3          # top-N + bottom-N contrastive
MAX_TRACE_MESSAGES_CHARS = 4000   # truncate each trace's messages to this
MAX_SOURCE_EXCERPT_CHARS = 8000   # truncate agent_loop.py to this


def _try_local_llm(prompt: str) -> str | None:
    """Call the local vLLM endpoint. OpenAI-compatible chat completions.

    Default proposer transport for air-gap-safe operation. A defense/gov
    prospect cannot use a self-tuning loop that phones home to Anthropic;
    the whole self-tuning pitch collapses the moment a remote API shows
    up in the loop.

    Uses `httpx` directly (already a drydock transitive dep) rather than
    pulling in the `openai` SDK. Endpoint from
    DRYDOCK_RESEARCH_PROPOSER_URL; model from
    DRYDOCK_RESEARCH_PROPOSER_MODEL (default 'gemma4').
    """
    try:
        import httpx
    except ImportError:
        print("  proposer: httpx not available — cannot call local LLM",
              file=sys.stderr)
        return None
    url = LOCAL_ENDPOINT.rstrip("/") + "/chat/completions"
    payload = {
        "model": LOCAL_MODEL,
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        r = httpx.post(url, json=payload, timeout=LOCAL_TIMEOUT_SEC)
        r.raise_for_status()
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        return content or None
    except Exception as e:
        print(f"  proposer: local LLM call failed: {e}", file=sys.stderr)
    return None


def _try_anthropic_sdk(prompt: str) -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        r = client.messages.create(
            model=OPUS_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
            timeout=OPUS_TIMEOUT_SEC,
        )
        if r.content and hasattr(r.content[0], "text"):
            return r.content[0].text.strip() or None
    except Exception as e:
        print(f"  proposer: SDK call failed: {e}", file=sys.stderr)
    return None


def _try_claude_cli(prompt: str) -> str | None:
    if shutil.which("claude") is None:
        return None
    try:
        r = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=OPUS_TIMEOUT_SEC,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except Exception as e:
        print(f"  proposer: CLI call failed: {e}", file=sys.stderr)
    return None


def _load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _results_ranked(results_tsv: Path, n: int = 10) -> list[dict]:
    """Return the n best-metric rows from results.tsv, most recent first
    within ties. Each row is a dict with the TSV columns."""
    if not results_tsv.exists():
        return []
    rows: list[dict] = []
    try:
        lines = results_tsv.read_text().splitlines()
        if len(lines) < 2:
            return []
        header = lines[0].split("\t")
        for line in lines[1:]:
            cols = line.split("\t")
            if len(cols) != len(header):
                continue
            rows.append(dict(zip(header, cols)))
    except Exception:
        return []
    try:
        rows.sort(key=lambda r: (-float(r.get("metric", "0") or "0"),
                                 -int(r.get("ts", "0") or "0")))
    except ValueError:
        pass
    return rows[:n]


def _load_trace_excerpt(exp_id: str) -> str:
    """Return a compact string summarizing one trace (summary.json +
    the last portion of messages.jsonl). Truncated to keep the proposer
    context manageable."""
    trace = RESEARCH_DIR / "traces" / exp_id
    if not trace.is_dir():
        return f"<trace {exp_id} missing>"
    parts: list[str] = []
    s = trace / "summary.json"
    if s.is_file():
        try:
            parts.append(f"summary: {s.read_text().strip()}")
        except OSError:
            pass
    m = trace / "messages.jsonl"
    if m.is_file():
        try:
            raw = m.read_text(errors="replace")
            if len(raw) > MAX_TRACE_MESSAGES_CHARS:
                raw = "...[truncated]...\n" + raw[-MAX_TRACE_MESSAGES_CHARS:]
            parts.append(f"messages-tail:\n{raw}")
        except OSError:
            pass
    return "\n\n".join(parts)


def _load_agent_loop_excerpt() -> str:
    """Return the `_check_tool_call_repetition` + `_sanitize_message_ordering`
    methods from agent_loop.py — the two most proposer-relevant pieces.
    These are FROZEN — proposer reads them to avoid proposing mutations
    that would fight the loop."""
    src = DRYDOCK_ROOT / "drydock" / "core" / "agent_loop.py"
    try:
        text = src.read_text()
    except OSError:
        return "<agent_loop.py unreadable>"
    # Keep it manageable — the full file is ~2500 lines. Take the two
    # methods that matter most.
    excerpt_lines: list[str] = []
    capturing = False
    indent = ""
    for line in text.splitlines():
        stripped = line.lstrip()
        if (stripped.startswith("def _check_tool_call_repetition")
                or stripped.startswith("def _sanitize_message_ordering")):
            capturing = True
            indent = line[: len(line) - len(stripped)]
            excerpt_lines.append(line)
            continue
        if capturing:
            if line.strip() == "" or line.startswith(indent + " ") or line.startswith(indent + "\t"):
                excerpt_lines.append(line)
            elif stripped.startswith("def "):
                capturing = False
    out = "\n".join(excerpt_lines)
    if len(out) > MAX_SOURCE_EXCERPT_CHARS:
        out = out[:MAX_SOURCE_EXCERPT_CHARS] + "\n...[truncated]..."
    return out


def _banned_entries(config_base: dict) -> list[dict]:
    """Surface entries flagged `banned = true` in config_base.toml so
    the proposer's prompt can name them explicitly. Returned as a list
    of {class, name} dicts (no values/bounds — the ban is about names,
    not ranges).
    """
    out: list[dict] = []
    for cls in ("knob", "harness_threshold", "admiral_detector"):
        for entry in config_base.get(cls, []):
            if entry.get("banned", False):
                out.append({"class": cls, "name": entry.get("name", "?")})
    return out


def _is_banned(m: dict, config_base: dict) -> bool:
    """Return True if the mutation targets an entry marked banned=true."""
    target = m.get("target")
    name = m.get("name", "")
    if target not in ("knob", "harness_threshold", "admiral_detector"):
        return False
    for entry in config_base.get(target, []):
        if entry.get("name") == name:
            return bool(entry.get("banned", False))
    return False


def _coverage_directive(coverage: dict[str, int]) -> str:
    """Given the recent-target distribution, return a directive the
    proposer prompt appends. When one class dominates, the directive
    becomes a REQUIREMENT (not a suggestion) to pick a different class.
    Soft hints didn't break Gemma 4's knob-rut; hard rotation does.
    """
    total = sum(coverage.values())
    if total < 10:
        return ""
    # If any single class >= 70% of recent, require a different class.
    MAX_DOMINANT = max(coverage.values()) if coverage else 0
    if MAX_DOMINANT / total < 0.7:
        return ("\nNote: coverage is reasonably balanced; propose "
                "whatever mutation is best-supported by the traces.")
    dominant = max(coverage, key=lambda k: coverage[k])
    alternatives = [t for t in ("knob", "harness_threshold",
                                "admiral_detector", "env_flag", "prompt")
                    if t != dominant]
    return (
        f"\n⛔ MANDATORY ROTATION: '{dominant}' has taken "
        f"{coverage[dominant]}/{total} of the last 15 mutations and "
        f"the leaderboard has plateaued. Your proposal this round "
        f"MUST have target ∈ {alternatives}. Proposing target="
        f"'{dominant}' again will be rejected. Pick the "
        f"most-likely-high-leverage target among the alternatives — "
        f"prompts are usually the highest-leverage untouched lever "
        f"when knobs have stopped producing gains."
    )


def _normalize_value_str(s: str) -> str:
    """Normalize a scalar value's string repr so '100' / '100.0' / "'100'"
    all compare equal. Used when diffing a fresh LLM proposal against the
    last N notes in results.tsv."""
    s = s.strip().strip("'\"")
    try:
        return str(float(s))
    except ValueError:
        return s


def _parse_proposal_note(note: str) -> tuple | None:
    """Extract (target, name, value_str) from a results.tsv note.

    LLM notes:    'llm <target>:<name> -> <value> (reason...)'
    Random notes: 'random <target>:<name>: <old> -> <new>'

    Returns None if the note doesn't match either shape.
    """
    if note.startswith("llm "):
        body = note[4:]
    elif note.startswith("random "):
        body = note[7:]
    else:
        return None
    for token in ("knob:", "harness_threshold:", "admiral_detector:",
                  "env_flag:", "prompt:"):
        if not body.startswith(token):
            continue
        target = token.rstrip(":")
        rest = body[len(token):]
        if " -> " not in rest:
            return None
        before, _, after = rest.rpartition(" -> ")
        name = before.split(": ", 1)[0] if ": " in before else before.strip()
        value_str = after.split(" (", 1)[0].strip()
        return (target, name.strip(), _normalize_value_str(value_str))
    return None


def _recent_proposal_tuples(results_tsv: Path,
                            n: int = 10) -> set[tuple]:
    """Return (target, name, value_str) tuples for the last N experiments.

    The class-level rotation and no-op checks each miss one class of
    fixation: the proposer repeatedly suggesting the exact same
    (target, name, value) that was already run (but isn't the *current*
    best value, so _is_noop_mutation misses it). Adding a tuple-level
    recency filter catches this cheaply.

    Observed case: 24/40 recent experiments were 'llm knob:wrap_up_warn_at
    -> 100', the same target+value repeated because the current best had
    wrap_up_warn_at=40 so the no-op filter didn't trigger. Every one of
    those 24 was a pure replay of an already-run experiment.
    """
    if not results_tsv.exists():
        return set()
    out: set[tuple] = set()
    try:
        lines = results_tsv.read_text().splitlines()
        if len(lines) < 2:
            return set()
        header = lines[0].split("\t")
        if "note" not in header:
            return set()
        note_idx = header.index("note")
        for line in lines[-n:]:
            cols = line.split("\t")
            if len(cols) <= note_idx:
                continue
            parsed = _parse_proposal_note(cols[note_idx])
            if parsed:
                out.add(parsed)
    except Exception:
        return set()
    return out


def _recent_mutation_coverage(results_tsv: Path,
                              n: int = 15) -> dict[str, int]:
    """Return {target_class: count} for the last N experiments' notes.

    Used as a coverage hint in the proposer prompt. When the proposer
    keeps proposing variations of the same 2-3 knobs ("knob-rut"), the
    leaderboard plateaus. Feeding back the recent-target distribution
    gives the model a reason to diversify without overriding its
    judgment.
    """
    if not results_tsv.exists():
        return {}
    try:
        lines = results_tsv.read_text().splitlines()
        if len(lines) < 2:
            return {}
        header = lines[0].split("\t")
        if "note" not in header:
            return {}
        note_idx = header.index("note")
        recent = lines[-n:]
        from collections import Counter
        counts: Counter[str] = Counter()
        for line in recent:
            cols = line.split("\t")
            if len(cols) <= note_idx:
                continue
            note = cols[note_idx]
            # Notes have shape "llm <target>:<name> -> ..." or
            # "random <target-hint>: ...". Extract the target class.
            for token in ("knob:", "harness_threshold:",
                          "admiral_detector:", "env_flag:", "prompt:"):
                if token in note:
                    counts[token.rstrip(":")] += 1
                    break
            else:
                if note.startswith("random "):
                    counts["random"] += 1
        return dict(counts)
    except Exception:
        return {}


def _current_values_table(config_best: dict) -> str:
    """Flatten the mutable current values into a simple two-column
    table. Gemma 4 reliably parses this; nested TOML values like
    `value = 40` get missed inside the larger config dump."""
    lines: list[str] = []
    for k in config_best.get("knob", []):
        val = k.get("value", k.get("default"))
        lines.append(f"  knob:{k['name']} = {val}")
    for h in config_best.get("harness_threshold", []):
        val = h.get("value", h.get("default"))
        lines.append(f"  harness_threshold:{h['name']} = {val}")
    for d in config_best.get("admiral_detector", []):
        val = d.get("value", d.get("default"))
        lines.append(f"  admiral_detector:{d['name']} = {val}")
    for name, v in (config_best.get("env_flags") or {}).items():
        lines.append(f"  env_flag:{name} = {v!r}")
    for name, entry in (config_best.get("prompts") or {}).items():
        if isinstance(entry, dict):
            src = entry.get("source_path", "")
            state = "CUSTOM" if src else "default (from installed package)"
            lines.append(f"  prompt:{name} = {state}")
    return "\n".join(lines)


def _build_prompt(config_base: dict, config_best: dict,
                  ranked_results: list[dict]) -> str:
    """Assemble the prompt the proposer sees. Concise — Opus is smart
    but the payload needs structure so it returns a parseable proposal."""
    top = ranked_results[:MAX_TRACES_EACH_SIDE]
    bottom = ranked_results[-MAX_TRACES_EACH_SIDE:] if len(ranked_results) >= MAX_TRACES_EACH_SIDE * 2 else []
    coverage = _recent_mutation_coverage(RESEARCH_DIR / "results.tsv", n=15)
    current_values = _current_values_table(config_best)

    domain_spec_path = RESEARCH_DIR / "domain_spec.md"
    claude_md_path = DRYDOCK_ROOT / "CLAUDE.md"

    def _read_head(p: Path, max_chars: int) -> str:
        try:
            s = p.read_text()
            return s if len(s) <= max_chars else s[:max_chars] + "\n...[truncated]..."
        except OSError:
            return f"<{p.name} unreadable>"

    top_traces_text = "\n\n---\n\n".join(
        f"[TOP exp_id={r.get('exp_id', '?')} metric={r.get('metric', '?')}]\n"
        f"{_load_trace_excerpt(r.get('exp_id', ''))}"
        for r in top
    )
    bottom_traces_text = "\n\n---\n\n".join(
        f"[BOTTOM exp_id={r.get('exp_id', '?')} metric={r.get('metric', '?')}]\n"
        f"{_load_trace_excerpt(r.get('exp_id', ''))}"
        for r in bottom
    )

    # Include the current gemma4.md baseline when the proposer might
    # want to mutate it. Opus needs to see what it's replacing.
    def _current_prompt(name: str) -> str:
        prompt_file = DRYDOCK_ROOT / "drydock" / "core" / "prompts" / f"{name}.md"
        try:
            return prompt_file.read_text()
        except OSError:
            return f"<{name}.md unreadable>"

    prompts_cfg = config_base.get("prompts", {})
    mutable_prompts = {n: e for n, e in prompts_cfg.items()
                       if isinstance(e, dict) and e.get("mutable")}
    prompt_bodies_section = ""
    if mutable_prompts:
        prompt_bodies_section = "\n# MUTABLE PROMPT BODIES (current)\n\n"
        for name in mutable_prompts:
            body = _current_prompt(name)
            prompt_bodies_section += (
                f"## prompts.{name}\n\n```\n{body}\n```\n\n"
            )

    def _active_names(section: list[dict]) -> list[str]:
        return [e["name"] for e in section
                if e.get("mutable", True) and not e.get("banned", False)]

    banned_listing = _banned_entries(config_base)

    return f"""You are Meta-Harness Proposer for drydock. Your job is to \
propose ONE mutation to the stress-harness config that will improve the \
`effective_rate` metric (done_per_minute, cliff at >50% failure).

You have read-only access to source code, a current-best config, an \
append-only results log, and recent execution traces. Mutations must stay \
within the bounded surface declared in config_base.toml.

# MUTATION SURFACE (bounded, declared in config_base.toml)

{json.dumps({
    "admiral_knobs": _active_names(config_base.get("knob", [])),
    "harness_thresholds": _active_names(config_base.get("harness_threshold", [])),
    "admiral_detectors": _active_names(config_base.get("admiral_detector", [])),
    "env_flags": list(config_base.get("env_flags", {}).keys()),
    "prompts": list(mutable_prompts.keys()),
}, indent=2)}{prompt_bodies_section}

# BANNED NAMES (mined out — do NOT propose these)

{json.dumps(banned_listing, indent=2) if banned_listing else '<none>'}

These names are intentionally retired from the mutation surface because \
prior experiments exhausted their leverage. Proposals targeting a banned \
name will be rejected. Pick from the MUTATION SURFACE above.

# RECENT MUTATION COVERAGE (last 15 experiments)

{json.dumps(coverage, indent=2) if coverage else '<no history yet>'}
{_coverage_directive(coverage)}

# CURRENT VALUES (the config as actually set right now)

{current_values}

⚠ CRITICAL: Your proposal's `value` field MUST DIFFER from the
current value above. Proposing the same value that is already set is
a no-op that wastes an experiment cycle. Read the list above carefully
before picking a value.

# CURRENT-BEST CONFIG (full TOML for reference)

```toml
{_read_head(RESEARCH_DIR / "config_best.toml" if (RESEARCH_DIR / "config_best.toml").exists() else RESEARCH_DIR / "config_base.toml", 4000)}
```

# DOMAIN SPEC (what we're optimizing; what's frozen)

{_read_head(domain_spec_path, 6000)}

# TOP PERFORMERS (contrast against BOTTOM below)

{top_traces_text or '<no traces yet — seed with random search first>'}

# BOTTOM PERFORMERS

{bottom_traces_text or '<not enough history yet>'}

# FROZEN REFERENCE: agent_loop.py key methods (DO NOT PROPOSE MUTATIONS TO THESE)

```python
{_load_agent_loop_excerpt()}
```

# YOUR TASK

Reply with EXACTLY this JSON object — no preamble, no markdown fence:

{{
  "target": "knob" | "harness_threshold" | "admiral_detector" | "env_flag",
  "name": "<field name exactly as declared in config_base.toml>",
  "value": <number for knobs/thresholds/detectors; string for env_flags>,
  "reason": "<≤200 char explanation rooted in the trace contrast>"
}}

Constraints:
- value MUST respect the min/max declared in config_base.toml for its field
- ONE mutation per call — pick the single highest-leverage one
- Prefer mutations with concrete evidence in the trace contrast; avoid \
random-seeming changes
- If you cannot identify a confident mutation, reply with exactly: NO_PROPOSAL
"""


def propose(config_base_path: Path, config_best_path: Path | None,
            results_tsv: Path) -> dict | None:
    """Build the proposer prompt, call Opus, parse the response.
    Returns the mutation dict or None."""
    config_base = _load_toml(config_base_path)
    config_best = _load_toml(config_best_path) if config_best_path and config_best_path.exists() else config_base
    ranked = _results_ranked(results_tsv, n=20)

    prompt = _build_prompt(config_base, config_best, ranked)

    # Local-first is non-negotiable: the defense/gov pitch (air-gapped
    # self-tuning agents) collapses the moment the proposer phones home.
    # Only fall back to Opus / Claude CLI if DRYDOCK_RESEARCH_ALLOW_OPUS=1
    # is explicitly set AND the local endpoint returned nothing useful.
    # The experimenter's default (--proposer local) skips opus entirely.
    response = _try_local_llm(prompt)
    if not response and os.environ.get("DRYDOCK_RESEARCH_ALLOW_OPUS") == "1":
        print("  proposer: local returned nothing, falling back to cloud "
              "(DRYDOCK_RESEARCH_ALLOW_OPUS=1). This is NOT airgap-safe.",
              file=sys.stderr)
        response = _try_anthropic_sdk(prompt) or _try_claude_cli(prompt)
    if not response:
        print("  proposer: no response from Opus", file=sys.stderr)
        return None
    response = response.strip()
    if response == "NO_PROPOSAL":
        return None

    # Extract JSON. Opus sometimes wraps in a fence despite instructions.
    if response.startswith("```"):
        lines = response.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        response = "\n".join(lines)
    try:
        mutation = json.loads(response)
    except json.JSONDecodeError:
        print(f"  proposer: response not parseable as JSON:\n{response[:400]}",
              file=sys.stderr)
        return None

    if not isinstance(mutation, dict):
        return None
    for required in ("target", "name", "value", "reason"):
        if required not in mutation:
            print(f"  proposer: response missing '{required}'", file=sys.stderr)
            return None

    # Validate the mutation against the declared bounds.
    if not _validate_mutation(mutation, config_base):
        print(f"  proposer: invalid mutation: {mutation}", file=sys.stderr)
        return None
    # Banned-name filter — entries marked banned=true in config_base.toml
    # are retired from the mutation surface. Reject the proposal so caller
    # falls through to random (which also honors the ban).
    if _is_banned(mutation, config_base):
        print(f"  proposer: banned-name rejection — "
              f"{mutation.get('target')}:{mutation.get('name')} is "
              f"retired; re-prompt or fall through to random",
              file=sys.stderr)
        return None
    # No-op filter — Gemma 4 has been proposing struggle_threshold=20
    # when current config already has struggle_threshold=40, which it
    # mis-cited from the leaderboard. Reject proposals that don't
    # actually change anything so we don't waste a kernel run.
    config_best = _load_toml(RESEARCH_DIR / "config_best.toml") \
        if (RESEARCH_DIR / "config_best.toml").exists() else config_base
    if _is_noop_mutation(mutation, config_best):
        print(f"  proposer: no-op mutation (value already set): "
              f"{mutation.get('target')}:{mutation.get('name')}="
              f"{mutation.get('value')}", file=sys.stderr)
        return None
    # Tuple-level recency filter — reject proposals whose exact
    # (target, name, value) was already run in the last 10 experiments.
    # Catches the narrow-exploration failure where Gemma 4 proposes the
    # same tuple 24+ times because the no-op filter only compares against
    # config_best, not the tried-and-discarded history.
    recent_tuples = _recent_proposal_tuples(results_tsv, n=10)
    key = (mutation.get("target"),
           str(mutation.get("name", "")).strip(),
           _normalize_value_str(str(mutation.get("value"))))
    if key in recent_tuples:
        print(f"  proposer: duplicate-proposal rejection — {key} was "
              f"already run in the last 10 experiments; falling through "
              f"to random", file=sys.stderr)
        return None
    # Mandatory rotation enforcement. If one class dominates recent
    # coverage, reject proposals in that class so caller falls back to
    # random search (which is guaranteed to pick a different target
    # eventually). Prompt-level hint alone hasn't been enough to break
    # Gemma 4's rut; this is the backstop.
    coverage = _recent_mutation_coverage(
        RESEARCH_DIR / "results.tsv", n=15)
    total = sum(coverage.values())
    if total >= 10:
        max_count = max(coverage.values())
        if max_count / total >= 0.7:
            dominant = max(coverage, key=lambda k: coverage[k])
            if mutation.get("target") == dominant:
                print(f"  proposer: mandatory rotation triggered — "
                      f"'{dominant}' dominates recent coverage "
                      f"({max_count}/{total}); rejecting same-class "
                      f"proposal", file=sys.stderr)
                return None
    return mutation


def _is_noop_mutation(m: dict, config_best: dict) -> bool:
    """Return True if the proposal sets a value equal to what's already
    in config_best — a waste of a kernel run."""
    target = m.get("target")
    name = m.get("name", "")
    value = m.get("value")
    if target in ("knob", "harness_threshold", "admiral_detector"):
        collection_key = {"knob": "knob",
                          "harness_threshold": "harness_threshold",
                          "admiral_detector": "admiral_detector"}[target]
        for entry in config_best.get(collection_key, []):
            if entry.get("name") == name:
                current = entry.get("value", entry.get("default"))
                try:
                    return float(current) == float(value)
                except (TypeError, ValueError):
                    return current == value
    if target == "env_flag":
        current = (config_best.get("env_flags") or {}).get(name)
        return current == value
    return False


def _validate_mutation(m: dict, config_base: dict) -> bool:
    target = m.get("target")
    name = m.get("name", "")
    value = m.get("value")
    if target == "knob":
        for k in config_base.get("knob", []):
            if k["name"] == name:
                try:
                    v = float(value)
                    return float(k["min"]) <= v <= float(k["max"])
                except (TypeError, ValueError):
                    return False
        return False
    if target == "harness_threshold":
        for h in config_base.get("harness_threshold", []):
            if h["name"] == name:
                try:
                    v = float(value)
                    return float(h["min"]) <= v <= float(h["max"])
                except (TypeError, ValueError):
                    return False
        return False
    if target == "admiral_detector":
        for d in config_base.get("admiral_detector", []):
            if d["name"] == name:
                try:
                    v = float(value)
                    return float(d["min"]) <= v <= float(d["max"])
                except (TypeError, ValueError):
                    return False
        return False
    if target == "env_flag":
        flags = config_base.get("env_flags", {})
        return name in flags and isinstance(value, str)
    if target == "prompt":
        prompts = config_base.get("prompts", {})
        entry = prompts.get(name)
        if not isinstance(entry, dict) or not entry.get("mutable"):
            return False
        # Prompt value must be a non-empty string; set a sanity cap so
        # a hallucinated 200KB prompt can't blow up context.
        return isinstance(value, str) and 0 < len(value) <= 50_000
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Meta-Harness Opus proposer.")
    ap.add_argument("--config-base", type=Path,
                    default=RESEARCH_DIR / "config_base.toml")
    ap.add_argument("--config-best", type=Path,
                    default=RESEARCH_DIR / "config_best.toml")
    ap.add_argument("--results-tsv", type=Path,
                    default=RESEARCH_DIR / "results.tsv")
    args = ap.parse_args()

    mutation = propose(args.config_base, args.config_best, args.results_tsv)
    if mutation is None:
        print("NO_PROPOSAL")
        return 1
    print(json.dumps(mutation, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
