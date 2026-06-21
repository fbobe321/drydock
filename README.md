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

Shipping. Published on PyPI as **`drydock-cli`** (v3.x). The Textual TUI is the
default surface: a scrolling transcript with streamed assistant text, collapsible
tool cards, a live nautical activity line, and a multi-line prompt. The agent
loop, OpenAI-compatible provider, two-tier compaction, and the core tools
(Read/Write/Edit/Bash/Glob/Grep) are in, with Gemma reliability hardening verified.

## Install

```bash
pip install drydock-cli
drydock
```

Requires Python 3.12+. From source instead:
`git clone https://github.com/fbobe321/drydock-v3.git && cd drydock-v3 && pip install -e .`

On first launch with no config, Drydock probes localhost for a running local
LLM (llama.cpp/vLLM `:8000`, Ollama `:11434`, LM Studio `:1234`) and wires up
the first one it finds — no account or API-key prompt. Override anytime with
`--model` / `--provider` / `--base-url` or `~/.drydock/config.toml`.

## Using it

Type a task and press **Enter**. Drydock reads/writes/edits files and runs
commands to do the work, showing each as a collapsible tool card.

- **Enter** submits · **Ctrl+J** newline (multi-line prompts)
- **↑ / ↓** recall command history (persists across sessions)
- **PgUp / PgDn** (and **Ctrl+Home/End**) scroll the transcript
- **Ctrl+O** expand/collapse tool output · **drag + Ctrl+C** copy a selection
- **Ctrl+C twice** (or **Ctrl+D**, `/quit`) to exit
- A live activity line shows progress while it works:
  `◡ Keelhauling…  (12s · ↓ 6.2k tokens · thinking with high effort)`
- Submit while it's working and the prompt **queues** (drains in order)
- Slash commands: `/model [name]` · `/cwd [path]` · `/undo` (revert last write)
  · `/back` (rewind last turn) · `/status` · `/clear` · `/help` · `/quit`

It honors `AGENTS.md` / `DRYDOCK.md` in the working directory for project
conventions.

## Safety

Two tiers, plus advisory guards — all designed so legitimate work is never
blocked:

- **Catastrophic denylist** — commands like `rm -rf /`, `mkfs`, raw block-device
  writes, and fork bombs are refused outright (never run).
- **Approval prompt** — sensitive-but-legitimate commands (`sudo`, package
  installs, network fetches, `git push`) pause for **Allow / Always / Deny**.
- **Advisory write guards** — Drydock flags (never blocks) Python syntax errors,
  stub-only files, imports of sibling modules that don't exist yet, bare
  `raise` outside an except, and refuses to write git conflict-marker content.

Point it at a local OpenAI-compatible endpoint (e.g. llama.cpp's `server-cuda`
serving Gemma 4 26B).

## Model server (reference setup)

Drydock is provider-agnostic, but it's tuned and measured against this rig:

- **Model:** Gemma-4-26B-A4B-it (26B MoE, ~4B active/token), Unsloth `Q3_K_M`
  GGUF, served by `ghcr.io/ggml-org/llama.cpp:server-cuda` with `--jinja`.
- **GPUs:** 2× NVIDIA RTX 4060 Ti 16GB. The Q3_K_M weights (~13 GB) fit on a
  **single 16 GB card**, so each GPU runs a **full, independent instance** —
  two cards give two parallel instances for throughput; the model is **not**
  tensor-split or "pooled" across both.
- **Context:** 64k (`-c 65536`) with `q8_0` KV-cache quantization
  (`-ctk q8_0 -ctv q8_0`) — 64k @ q8 fits roughly the same VRAM as 32k @ f16.
- **Throughput:** ~**64 tok/s** decode, ~94 tok/s prompt (per instance, Q3_K_M).
- **Minimum:** any single 16 GB+ CUDA card runs it.

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
