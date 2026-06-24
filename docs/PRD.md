# Drydock v3 — Product Requirements

Status: SHIPPING (v3.0.38, CI green). Supersedes the v2 line.
Owner: Frank Bobe III. License: Apache-2.0 (own copyright).

> **Progress (2026-06-24):** Model swapped 26B-A4B → **dense Gemma-4-31B**
> (64K, fleet-wide) to kill the agentic looping. Shipped v3.0.33–38
> (timeout msg, vision input, STOP process-tree kill, vision path-strip, CI).
> The terminal-bench (`harbor`) eval harness now runs end-to-end on the 31B
> (was blocked by ufw/docker container networking — fixed). Baseline to beat:
> v2+26B = 15/47 (31.9%); opencode = 51.7%. **Full resume state, harness
> setup, and the overnight eval plan are in `RESUME.md` (read first) and
> `/tmp/overnight_tbench_plan.md`.**

## 1. Why v3 exists

v2 was a fork of Mistral's `mistral-vibe`. It carried inherited cloud code —
a "teleport" feature that shipped a user's GitHub token to a hardcoded
third-party host (`globalaegis.net`), default-on telemetry that mailed the
API key off-box, and a startup account-check. PyPI quarantined the project
in 2026-06 for exactly that shape. The endpoint turned out to be present in
Mistral's own tagged v2.4.2 release (verified against the upstream tag), so
it wasn't injected by us — but we shipped it unaudited, and that's the
lesson: **a forked codebase you don't fully own is a liability you can't
fully see.**

v3 owns the lineage end to end. It is an original, clean-room implementation
under our own copyright, with a pre-release scanner that makes the exfil
shape un-shippable.

## 2. Vision

A local, provider-agnostic **terminal coding agent** that feels like a
top-tier CLI agent and runs entirely against a **local LLM** — primary
target **Gemma-4-26B-A4B** served by llama.cpp. No accounts, no telemetry,
no cloud. It builds real projects from a prompt, reliably, on a single
workstation (2× RTX 4060 Ti 16 GB).

## 3. Principles (non-negotiable)

1. **Clean provenance.** No code copied from any third party — not Mistral's
   mistral-vibe, not Anthropic's Claude Code (including the reverse-engineered
   extraction at `/data3/claude-code/`), not any decompiled artifact. We
   emulate UX *conventions*; we never copy code, prompt wording, or branding.
2. **Local-only data plane.** The only outbound network call is to the
   user-configured LLM endpoint (a config value). No telemetry, no phone-home,
   no cloud teleport, no hardcoded third-party hosts, no credential
   transmission. Ever.
3. **The scanner is law.** `scripts/security_scan.py` gates every release and
   blocks publish on any exfil shape (non-allowlisted host touching
   creds/network, or decode-then-exec). It runs in CI and at release.
4. **Advisory, never blocking.** Safety/loop mechanisms inject better context;
   they never hard-stop legitimate work. Circuit breakers are banned (they
   were net-negative in v2).
5. **TUI is the product.** Headless/one-shot exists for scripting, but the
   hands-on TUI is the experience and the source of truth for bugs.
6. **Prove it in the TUI.** No harness-side feature is "done" until driven
   hands-on via tmux against the real model with capture-pane evidence.

## 4. Target user & hardware

A developer running a local model who wants an agent that builds projects
without sending code or credentials anywhere. Reference rig: Ubuntu, 2× RTX
4060 Ti 16 GB, Gemma-4-26B-A4B (Q3_K_M) via llama.cpp `server-cuda`.

## 5. Experience (Claude-Code feel, nautical identity)

Emulated from first principles, not copied:

- A scrolling chat transcript with streamed assistant text and **collapsible
  tool-call cards** (compact by default; expand for full args/output).
- Inline, advisory **permission prompts** for risky tools, with a remembered
  allowlist.
- **Slash commands** for control (`/help`, `/undo`, `/clear`, `/model`,
  `/goal`, `/quit`).
- A prompt box with history, multiline (Ctrl+J), and "type while busy"
  injection into the running turn.

Identity is **ours and nautical**: ⚓ Drydock banner, harbor/dock/anchor
metaphors, our own color theme. No "Claude" naming, no Anthropic branding,
no copied ASCII or message strings.

## 6. Reliability requirements (Gemma-26B-specific, all our own logic)

- **Non-streaming whenever tools are offered** (Gemma corrupts tool-call JSON
  when streamed).
- **Adaptive thinking budget:** HIGH for planning/first user turn, OFF for
  routine writes, LOW for recovery.
- **Strip leaked thinking tokens** (`<|channel>…<channel|>` variants) before
  the next call.
- **Disable loop-prone tools** for Gemma (ask_user_question, todo, task_*,
  invoke_skill, tool_search).
- **Short "act immediately" system prompt** selected by model name.
- **Loop detection guides, never stops:** prune exact repeats, inject an
  advisory nudge; only hard stop is a max-tool-turns ceiling; loop-breakers
  return a result, never raise.
- **Write hardening:** overwrite-by-default; refuse known-broken outputs
  (main-module entry, missing-sibling imports, stub classes) with actionable
  feedback; escalating dedup nudge.
- **Edit hardening:** handle the three search/replace failure modes (missing
  path, raw code without markers, already-applied) with fallbacks, not errors.
- **Two-tier compaction** (normal + emergency) — already present in v3.

## 7. Architecture

Build on the existing clean v3 spine (~1k LOC): `agent.py` (loop + event
stream), `providers.py` (OpenAI-compatible client for llama.cpp),
`compaction.py`, `tool_registry.py`, `tools/` (read/write/edit/bash/glob/
grep). Add: reliability core (§6), a Textual TUI (§5), config/onboarding,
and the release pipeline.

## 8. Non-goals (for now)

No cloud sync, no accounts, no managed service, no benchmark/eval harnesses
(improve by real hands-on use, not score-chasing), no SWE-bench-style
batch runners. Multimodal/vision is optional and deferred.

## 9. Milestones

- **M0 — Foundation (done):** standalone Apache-2.0 repo, LICENSE/NOTICE,
  scanner, design doc, this PRD.
- **M1 — Reliability core:** §6 implemented + unit tests; passes a scripted
  build of one medium PRD headlessly.
- **M2 — TUI:** §5 implemented; first real tmux shakedown against Gemma-26B.
- **M3 — Config/onboarding/packaging:** local autodetect, llama.cpp defaults,
  scanner-gated release pipeline.
- **M4 — Validation & launch:** PRD shakedown suite (401–405) passes; website
  updated; publish to PyPI once the account is restored to good standing.

## 10. Risks

- **PyPI access.** Publishing depends on account reinstatement (incident in
  progress). Mitigation: ship via GitHub/Docker meanwhile; keep one account,
  full transparency.
- **TUI scope.** A good TUI is the largest chunk; risk of over-building.
  Mitigation: ship a minimal usable transcript+cards first, iterate from real
  use.
- **Model variance.** Same prompt can pass/fail run-to-run. Mitigation:
  report both numbers; rely on advisory hardening, not brittle gates.
