# Drydock v3 — Product Requirements

Status: SHIPPING (v3.0.44, CI green). Supersedes the v2 line.
Owner: Frank Bobe III. License: Apache-2.0 (own copyright).

> **Progress (2026-06-24, late):** Model is the **dense Gemma-4-31B** (64K,
> fleet-wide) — it killed the 26B-A4B agentic looping (confirmed with data,
> see below). Shipped **v3.0.39–44** today, all CI-green:
> - v3.0.39 concrete default `base_url` (config shows the endpoint);
>   v3.0.40 Python floor 3.12→3.11 (v2-upgrade compat); **v3.0.41 active
>   v2→v3 config migration** (back up legacy/foreign config, write a fresh
>   editable one — verified on a real v2 config on the Windows 3090).
> - **v3.0.42** `-p`/one-shot surfaces an unreachable LLM as the actionable
>   message + exit 2 (was a raw traceback that buried it — exactly what harbor
>   logged when a backend died); purged `qwen` sample strings per the
>   no-Chinese-models rule.
> - **v3.0.43** plan panel cleared at the start of each user turn (stale plan
>   no longer lingers / can't fire a stale continue-nudge) — found by *using*
>   the TUI, verified live.
> - **v3.0.44** `-p` trace logs tool inputs + outcomes (so a timed-out run is
>   diagnosable, not an opaque wall of `[Bash]`).
> - Harness-side (in `harbor_fork`): adapter now **skips unreachable backends**
>   in the rr pool (a frozen Jetson silently errored ~¼ of a run); a
>   never-404 wheel launch helper at `/data3/tbench_local/tbench_launch_lib.sh`.
>
> **terminal-bench (harbor) results, dense 31B, full 89-task corpus:**
> - **pass@1 = 21/89 ≈ 23.6%** (clean 3-box run, Jetson excluded for
>   speed-fairness). On v2's own passing set (V2PASSED10) the 31B holds
>   **8/10**.
> - **pass@3 (in progress)** with extended agent-timeout (`--agent-timeout-
>   multiplier 6`, up from 4) is climbing past **29%** with most trials still
>   pending — job `drydock_PASS3_v3.0.44_mult6_3box`, monitor
>   `/tmp/pass3_progress.log`.
> - Baselines: v2+26B = 31.9% (easier 47-subset, pass@5-ish); opencode = 51.7%.
>   The gap is concentrated in frontier tasks (compiler builds, ray tracers,
>   ARC-AGI) that exceed the 31B's *speed* at these timeouts — **genuine slow
>   work, not loops** (diagnosed).
>
> **Doom-loop status vs 26B-A4B (data, 219–239 trials + hands-on TUI):**
> the dense 31B does **not** reproduce the 26B's hard tool-loop (e.g. pytest
> 180×). The byte-identical tool guard (`IDENTICAL_REPEAT_CAP=8`) fired on
> only **36/239 trials and caps at 8**; ~85% never trip it; 0 loops across
> ~8 hands-on TUI tasks. One *different*, rare failure remains: **text-
> repetition collapse** (1 task — make-mips-interpreter emitted `295:` ×1365),
> which the tool guard doesn't see. Root cause: llama.cpp server runs with no
> repetition control. **Queued fix (task #41):** add `--repeat-penalty 1.1`
> (+ optional `--dry-multiplier 0.8`) server-side, and/or a drydock-side
> text-loop guard. Deferred until no tbench run is mid-flight.
>
> **Direction next:** (1) land the pass@3 headline + the list of which tasks
> the extended timeout rescued; (2) ship #41 (repetition control) once the
> fleet is free; (3) keep improving by real hands-on TUI use (the rule that
> found the stale-plan bug) — not score-chasing.
> **Full resume state + harness setup in `RESUME.md` (read first).**

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
target the **dense Gemma-4-31B** (QAT, 64K) served by llama.cpp (swapped
from 26B-A4B, which looped; see the progress note). No accounts, no
telemetry, no cloud. It builds real projects from a prompt, reliably, on a
single workstation (2× RTX 4060 Ti 16 GB).

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
