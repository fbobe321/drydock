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


def _build_prompt(config_base: dict, config_best: dict,
                  ranked_results: list[dict]) -> str:
    """Assemble the prompt the proposer sees. Concise — Opus is smart
    but the payload needs structure so it returns a parseable proposal."""
    top = ranked_results[:MAX_TRACES_EACH_SIDE]
    bottom = ranked_results[-MAX_TRACES_EACH_SIDE:] if len(ranked_results) >= MAX_TRACES_EACH_SIDE * 2 else []

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

    return f"""You are Meta-Harness Proposer for drydock. Your job is to \
propose ONE mutation to the stress-harness config that will improve the \
`effective_rate` metric (done_per_minute, cliff at >50% failure).

You have read-only access to source code, a current-best config, an \
append-only results log, and recent execution traces. Mutations must stay \
within the bounded surface declared in config_base.toml.

# MUTATION SURFACE (bounded, declared in config_base.toml)

{json.dumps({
    "admiral_knobs": [k["name"] for k in config_base.get("knob", [])],
    "harness_thresholds": [h["name"] for h in config_base.get("harness_threshold", [])],
    "admiral_detectors": [d["name"] for d in config_base.get("admiral_detector", [])],
    "env_flags": list(config_base.get("env_flags", {}).keys()),
    "prompts": list(mutable_prompts.keys()),
}, indent=2)}{prompt_bodies_section}

# CURRENT-BEST CONFIG

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
    return mutation


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
