# Drydock v3 — Gemma-26B Coding Harness: Design & Provenance

Drydock v3 is a clean-room, Apache-2.0 (own-copyright) coding agent tuned for
a local **Gemma-4-26B-A4B** model served via llama.cpp. It replaces the
shipping v2 line, which was a fork of Mistral's `mistral-vibe` and carried
inherited cloud/phone-home code that got the PyPI account quarantined
(2026-06). v3 exists to own the lineage end to end.

## Provenance rules (non-negotiable — this is the whole point of v3)

1. **No copied code.** Not from Mistral's `mistral-vibe`, not from Anthropic's
   Claude Code, not from any extracted/decompiled/source-mapped artifact.
   Ideas, architecture, and UX *conventions* are fair to learn from;
   verbatim code, distinctive prompt/message wording, and branding are not.
2. **`/data3/claude-code/` is off-limits as a code source.** It is an
   *unofficial reverse-engineered extraction* of Anthropic's proprietary
   Claude Code (`@anthropic-ai/claude-code`). We emulate the *feel* of a
   good terminal agent from first principles; we do not read it to copy.
3. **No telemetry, no phone-home, no cloud teleport, no hardcoded
   third-party hosts, no credential transmission.** The only outbound calls
   are to the user-configured LLM endpoint (a config value, never a baked-in
   host).
4. **`scripts/security_scan.py` gates every release.** It blocks publish if
   any file calls a non-allowlisted host or does decode-then-exec. This is a
   permanent gate, mirrored from the v2 remediation.
5. **License:** Apache-2.0, copyright Frank Bobe III. `NOTICE` carries only
   our own attribution — no Mistral/Anthropic notices, because no
   Mistral/Anthropic code is present.

## What v3 has (the clean spine + TUI + ported hardening)

- `agent.py` — agent loop, event stream (ToolStart/ToolEnd/TurnDone),
  adaptive reasoning per turn, hallucinated-tool redirect, text-form tool-call
  recovery, `/back` support (drop_last_turn).
- `providers.py` — OpenAI-compatible client; non-streaming for Gemma tool
  turns; fast unreachable-endpoint error; forwards `reasoning_effort`.
- `compaction.py` — two-tier compaction (normal + emergency), oldest-first
  drop, robust context-overflow detection.
- `tool_registry.py` / `tools/__init__.py` — Read/Write/Edit/Bash/Glob/Grep,
  with the safety + guard layers below; undo journal (`/undo`).
- `bash_safety.py` — catastrophic-command denylist + sensitive-command
  approval tier.
- `guards.py` — advisory write guards (syntax, main-entry, stub, sibling
  imports, bare-raise) + conflict-marker refusal.
- `loop_detect.py` — exact-repeat nudges + same-path write-thrash advisory.
- `tuning.py` — Gemma tuning (non-streaming, token stripping, tool gating,
  adaptive-thinking policy, simplified prompt).
- `config.py` / `detect.py` — `~/.drydock/config.toml` + first-launch
  local-LLM autodetection.
- `tui/` — Textual TUI: transcript, streamed text, tool cards, multi-line
  prompt with history, slash commands, approval modal.
- `cli.py` — `--cli` plain mode + `-p` one-shot (the TUI is the default).

## Gemma-26B lessons — reimplemented (from v2 real-use, all our own logic)

All implemented in v3's own style (not pasted from v2):

- **Non-streaming for tool calls** (Gemma corrupts tool-call JSON streamed). ✔
- **Adaptive reasoning budget** — HIGH to plan, LOW for routine continuation;
  wired via `reasoning_effort`, verified correctness-preserving. ✔
- **Strip thinking/special tokens** — `<|channel>…</`, `<|tool_call>…`, and
  generic `<|…|>` leaks. ✔
- **Disable loop-prone tools for Gemma** + redirect hallucinated tool names. ✔
- **Simplified "act immediately" system prompt.** ✔
- **write overwrite=True by default**; reject blank/dir paths. ✔
- **Hardened edits** — already-applied no-op, fuzzy match, directory→file
  inference, conflict-marker refusal. ✔
- **Write guards** — main-entry, missing-sibling imports, stub-only, bare
  raise; all advisory. ✔
- **Loop detection guides, never blocks** — exact-repeat + write-thrash
  nudges; only hard stop is max-tool-turns. Circuit breakers banned. ✔
- **Loop-breakers return a result, never raise.** ✔

## UX target: Claude-Code *feel*, nautical *identity*, clean implementation

Emulate the proven terminal-agent experience (these are general conventions,
implemented from scratch):

- A scrolling chat transcript: user turns, streamed assistant text, and
  **collapsible tool-call cards** (compact by default, expand for full
  output) — drives the "watch it work" feel.
- Inline **permission prompts** for risky tools (write/bash), advisory not
  blocking by default, with a remembered allowlist.
- **Slash commands** for control (`/help`, `/undo`, `/clear`, `/model`, …).
- A persistent prompt box with history; "type while busy" injects into the
  running turn at the next boundary.

Identity stays **nautical and ours**: the ⚓ Drydock banner, harbor/dock/anchor
metaphors in status and command names, our own color theme. No Anthropic
branding, no "Claude" naming, no copied ASCII art or message strings.

## Roadmap

- **Phase 0 — done.** LICENSE/NOTICE, pyproject license, security scanner +
  release gate, this doc.
- **Phase 1 — done.** Reliability core: loop detection, edit/write guards,
  non-streaming + adaptive thinking + token strip + simplified prompt, plus
  the full v2 hardening port (sibling/bare-raise/conflict-marker guards,
  hallucinated-tool redirect, write-thrash advisory, blank-path guard).
- **Phase 2 — done.** Textual TUI: transcript, tool cards, multi-line prompt
  with history, slash commands (`/model /cwd /undo /back /status /clear /help`),
  approval modal, config + first-launch autodetect, nautical theme.
- **Phase 3 (as earned):** selective extras (retrieval, more tools) — only what
  real use justifies. PyPI/Docker distribution returns on account reinstatement.
