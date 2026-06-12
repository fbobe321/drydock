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

## What v3 already has (the clean spine, ~1k LOC)

- `agent.py` — agent loop, event stream (ToolStart/ToolEnd/TurnDone).
- `providers.py` — OpenAI-compatible streaming client (works against
  llama.cpp's OpenAI server).
- `compaction.py` — two-tier compaction (normal + emergency). Keep.
- `tool_registry.py` — tool plugin registry.
- `tools/__init__.py` — read, write, edit (fuzzy), bash, glob, grep.
- `cli.py` — plain interactive/oneshot CLI (to be superseded by the TUI).

## Gemma-26B lessons to reimplement (from v2 real-use, all our own logic)

These are *behaviors we learned the model needs*, to be re-expressed in v3's
style — not pasted from v2:

- **Non-streaming for tool calls.** Gemma-4 corrupts tool-call JSON when
  streamed; use non-streaming whenever tools are offered.
- **Adaptive thinking budget.** HIGH for planning/first-user-turn, OFF for
  routine writes, LOW for error recovery. HIGH-on-every-turn burns
  30–120 s/turn for no gain.
- **Strip thinking tokens.** The model leaks `<|channel>…<channel|>`
  variants; filter them out of stored history before the next call.
- **Disable loop-prone tools for Gemma.** `ask_user_question`, `todo`,
  `task_*`, `invoke_skill`, `tool_search` cause loops/validation errors.
- **Simplified system prompt.** A short "act immediately" prompt beats a
  long capabilities prompt for this model.
- **write overwrite=True by default.** Gemma re-writes the same file; refusing
  caused error loops.
- **Hardened edits.** search/replace fails three ways — drops the path, sends
  raw code without markers, retries an already-applied edit — each needs a
  fallback rather than an error.
- **Write guards.** Refuse known-broken outputs (main-module entry mistakes,
  missing-sibling imports, stub classes) with actionable feedback.
- **Loop detection guides, never blocks.** Prune exact repeats and inject an
  advisory nudge; the only hard stop is a max-tool-turns ceiling. Circuit
  breakers were net-negative in v2 and are banned.
- **Loop-breakers return a result, never raise.** Raising a tool error on a
  long task spawns its own loop.

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

- **Phase 0 (now):** LICENSE/NOTICE, pyproject license, security scanner +
  release gate, this doc.
- **Phase 1:** reliability core (loop detection, edit/write guards,
  non-streaming + adaptive thinking + token strip + simplified prompt for
  Gemma).
- **Phase 2:** Textual TUI with the UX above + nautical theme.
- **Phase 3 (as earned):** selective extras (graphrag, more tools, more slash
  commands) — only what real use justifies.
