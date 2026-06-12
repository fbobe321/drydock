# ⚓ Drydock

A local-first, provider-agnostic **terminal coding agent** for your own LLM.
No accounts, no telemetry, no cloud — the only network call it makes is to
the model endpoint you configure. Primary target: **Gemma-4-26B-A4B** served
by llama.cpp on a single workstation.

> **v3 — clean-room rebuild.** Drydock is being rebuilt as an original,
> Apache-2.0 codebase owned end to end (no upstream fork). Every release is
> gated by a credential-exfiltration scanner that blocks anything reaching
> off-box. See [`HARNESS_DESIGN.md`](HARNESS_DESIGN.md) and
> [`docs/PRD.md`](docs/PRD.md).

## Why

A coding agent should build real projects from your machine without sending
your code or credentials anywhere. Drydock runs entirely against a local
model, feels like a first-class terminal agent, and keeps its data plane on
your box.

## Status

Rebuild in progress. The clean spine works (agent loop, OpenAI-compatible
provider for llama.cpp, two-tier compaction, core tools). Reliability
hardening for Gemma and the Textual TUI are landing next. PyPI/Docker
distribution returns once the rebuild lands.

## Install (from source for now)

```bash
git clone https://github.com/fbobe321/drydock.git
cd drydock
pip install -e .
drydock
```

Point it at a local OpenAI-compatible endpoint (e.g. llama.cpp's
`server-cuda` serving Gemma 4 26B).

## Principles

- **Clean provenance** — original code only; nothing copied from any other
  project.
- **Local-only data plane** — no telemetry, no phone-home, no hardcoded
  third-party hosts, no credential transmission.
- **Advisory, never blocking** — loop/safety mechanisms inject better
  context; they never hard-stop legitimate work.
- **The scanner is law** — `scripts/security_scan.py` gates every release.

## Security scan

```bash
python3 scripts/security_scan.py drydock/      # scan the source tree
python3 scripts/security_scan.py dist/*.whl    # scan a built wheel
```

Exit 2 (HIGH finding) blocks a release.

## License

Apache-2.0, © 2026 Frank Bobe III. See [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE).
