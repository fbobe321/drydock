# Drydock v3 — Product Requirements

Status: SHIPPING (v3.1.8, on PyPI + GitHub). Supersedes the v2 line.
Owner: Frank Bobe III. License: Apache-2.0 (own copyright).

> **Progress (2026-08-20) — MoE fast-generator experiment (throughput lever for the distillation loop).**
> Self-distillation has been throughput-starved (the dense 31B is slow; hard tasks flatline and yield no
> data). New lever: use the fast **Gemma-4 26B-A4B MoE (~4B active, ~4× the 31B's tok/s)** as a
> high-throughput GENERATOR on *easier* problems, then train the mission's 31B dense on those verified
> traces (same Gemma-4 family ⇒ format-native). This is a deliberate shift from *self*-distillation to
> cross-model (26B→31B) throughput; the honesty bar holds (the 31B still solves the eval itself). **Gate
> passed:** the MoE was originally benched for loop-collapse on hard tasks, but on easy tasks it engages
> cleanly — smoke through the real TUI solved fix-git (2/2 r1) and regex-log (r2) with no looping, at ~4×
> speed. **Open questions being measured:** does distilling diverse *easy* traces generalize to *hard*
> held-out tasks (headroom), and does distilling *down* from a weak model regress the 31B (needs a
> generalization arm + regression guard + a self-trace control). Collect stage running; train/eval pending
> a free trainer. Full detail: `RESUME.md` 2026-08-20 block. See [[project_compass_mission]].

> **Progress (2026-08-19) — CAMPAIGN SUBSTRATE UPGRADED TO Terminal-Bench 2.1.** The unattended
> tbench-2 completion campaign now runs against **Terminal-Bench 2.1**, upstream's more-verified
> iteration of 2.0: same 89 task names (drop-in), fixing 28/89 tasks (external-dependency, resource
> budget, and misspecification bugs) plus reward-hack hardening. This matters for the mission — 2.1
> guarantees *no task is unsolvable-by-bug*, so an **honest 100% is attainable** (it was not on 2.0,
> where several fleet "flatlines" were broken tasks, not hard ones). Vendored from
> `harbor-framework/terminal-bench-2-1` into the canonical task path (2.0 archived); 13 changed-and-
> solved tasks invalidated for re-verification; the 11 parked flatlines unparked to retry on 2.1.
> Honest post-switch baseline: **48/89** (2.0 read 60; the delta re-verifies). All steps reversible
> (snapshots retained). Full detail: `RESUME.md`. See [[project_compass_mission]].

> **Progress (2026-08-09) — SELF-DISTILLATION RESEARCH ARC + 3-BOX FLEET; heredity
> generalization wall CONFIRMED at 2× corpus.** (Full blow-by-blow: `RESUME.md` + the
> `project_*` memories; this is the product-level summary.) Since v3.1.x the research
> effort has driven drydock through the real TUI to test whether a local Gemma-4-31B can
> self-improve. Key results:
> - **Ratchet (Dawkins cumulative selection)** shipped as native `/ratchet` + a research
>   collector. Plain pawl earns real across-rounds cracks (llm-inference r6, etc.); the
>   "evolutionary" eratchet (QD/fan-out/crossover) fires but only MATCHES the plain pawl —
>   no unique cracks. **Finer-grained fitness** (2nd lever): lexicographic (passed, partial)
>   from CTRF sub-goal signal, so within-task selection has a gradient (no plateau-cliff).
> - **REFRAME:** the ratchet is evolution with HEREDITY cut — solves never compound into the
>   weights. The real lever = **self-distillation write-back** (train verified solves back in).
> - **HEREDITY EXPERIMENT — the mission's core question, answered NEGATIVE (clean):** train a
>   LoRA on condensed verified solves (matched HF base) → eval FROZEN held-out. v1 (16 traces,
>   loss→0.016) = **0/6 transfer**. v2 (31 traces, 2× size + 2× distinct-task diversity,
>   loss→0.009) = **STILL 0/6**, same mild regression. Doubling+diversifying the corpus did NOT
>   generalize. Tell: 31 diverse tasks speciate into 1 cluster because the *condensed format* is
>   uniform → the model memorizes format→solution, not skill. **⇒ next lever is NOT more data:**
>   (a) speciate on the raw INSTRUCTION → per-species adapters; (b) change the training TARGET
>   toward reasoning/skill; or (c) pivot.
> - **INFRA — 3-box ratchet fleet.** `.22` 2×4060Ti (llama.cpp, train + ddt client host),
>   `.21` vLLM, and NEW **`.20` Quadro RTX 8000 48GB**. The single 48GB card holds the whole
>   4-bit 31B on ONE GPU → **certified as a dedicated trainer** (QLoRA loss-decreasing smoke;
>   no `device_map` naive-pipeline waste that idles a 4060Ti), and serves/collects when not
>   training (escalation sweep of near-miss partials at 10 rounds). **RTX 8000 inference speed:
>   ~1.3× the 2×4060Ti tensor-split under matched load (19.2 vs 14.3 tok/s), ~1.9× idle (~28 vs
>   ~15 tok/s)** — modest for single-stream; the real win is training + a 3rd independent lane.


> **Progress (2026-07-10/12) — GOVERNED-RUNTIME backbone shipped (v3.0.116-133):** The
> Agent-Buildout PRD core landed incrementally (no rewrite): structured task state (survives
> compaction), verification gating + evidence (no self-declared done on a red check), durable
> event log + task reconstruction, phase tracking, rolling plan, and structured ToolResults.
> Plus a 10-task ML suite (gemma 9-10/10 via the real TUI), the recipe KB, ML skills, and
> clean-room /nist-ai-rmf + /nist-csf governance skills. 594 tests.

> **Progress (2026-07-08/09) — over-think handling, native Windows, GraphRAG, Screenshot,
> and the ACTIVATION-STEERING investigation (v3.0.107–3.0.115):**
> - **Over-think interrupt (3.0.107)** — escalating stall-retry → "decisive mode" (forcing
>   suffix + max_tokens cap + low effort) so gemma can't burn a 5k-token no-action turn.
> - **Repetition detector (3.0.115)** — CONTENT trigger for the interrupt: fires on a genuine
>   pure-repetition loop (`runaway_repetition_len`, 6+ reps/600+ chars), never on productive
>   reasoning — the precise, no-false-positive version of the wall-time trigger.
> - **Native Windows (3.0.108–111)** — runs in PowerShell/cmd, no WSL/bash; `_detect_shell`,
>   `DRYDOCK_SHELL` override, WINDIR-based detection, `/shell` diagnostic, shell-aware label.
> - **GraphRAG (3.0.112–113)** — quote-stripping path fix + per-file isolation + `BuildKnowledge`
>   tool (agent builds the KB itself). **Screenshot tool (3.0.114)** — capture screen → vision.
> - **🎯 Activation steering PROVEN but net-negative on pass-rate.** llama.cpp control vectors
>   (`--control-vector-scaled`); v2 (30 clean pairs, layers 20-45) is coherent + cuts over-think
>   tokens ~2x. But the **RSI measurement loop** (baseline-vs-intervention through the real TUI,
>   `classify.py`) showed, across 5 tasks: steering recovered **0/3** failures and **regressed 2**
>   passing tasks; wall-time interrupt ~net-zero and reliably regressed a productive-reasoning task.
>   **Finding: on this set gemma's failures are CAPABILITY-bound, not behavioral** — all three
>   interventions cut over-thinking but none reliably convert a fail→pass. The "wasted-capability"
>   slice steering can recover is ~0%. (n=5, single-run — directional.) The repetition detector is
>   the one behavioral win kept: precise + safe, catches degenerate loops with no regressions.
>   Artifacts + full numbers: `/data3/build/steer/STEERING_RESULTS.md`.
> - **Next hypothesis:** pass-rate needs INFORMATION not behavior-shaping — few-shot task
>   exemplars, retrieved tool recipes (GraphRAG), environment priming, requirement self-check.
> 544 tests. GitHub push token was rotated (old one expired); PyPI + GitHub back in sync.

> **Progress (2026-07-06) — full tool I/O-path + arg hardening (v3.0.93–3.0.103):** Found
> by directly PROBING drydock's own tool I/O paths (higher-yield than grinding hard
> tbench tasks, which stress the model, not drydock — cobol/db-wal ran the toolchain
> cleanly, model-limited, no harness bug). Seven fixes — `tool_bash`/grep/read/write
> are now the most-hardened surface:
> - **v3.0.98–99 wrong-type tool args** — local models send args as the wrong type;
>   Write content / Edit new_string as a list/int and Read file_path as a list raised
>   uncaught TypeErrors (tools must never raise). Shared coercers `_as_text`
>   (list→newline-join), `_as_str_arg` (unwrap single-elem list), `_coerce_int`
>   applied across write/edit/read/grep.
> - **v3.0.100–103** STOP preserves partial output (operator); Edit honors
>   **replace_all** (was ignored → multi-match loop); Glob no-crash on missing
>   pattern; wrong-type args coerced in GitCommit/Knowledge/Consult/Web*. All 23
>   tools audited for missing + wrong-type args.
> - **v3.0.97 Grep errors + grep/Read binary-safety** — an invalid regex (grep
>   exit ≥2, error on stderr) was reported as "(no matches)", a wrong negative the
>   model trusts; now returns the error. grep decodes errors="replace"; Read drops NUL.
> - **v3.0.93 binary/non-UTF8 output** — `text=True` strict decode raised
>   UnicodeDecodeError inside the reader thread; the thread died and the agent got
>   "(no output)", losing even the text parts of mixed output. Now `errors="replace"`.
> - **v3.0.94 output sanitization** — strip ANSI escape sequences (forced-colour
>   noise) and drop NUL bytes (trip some LLM servers' JSON) before the model sees it.
> - **v3.0.95 partial output on timeout** — a command that printed results then hung
>   lost them; now the pre-timeout output (tail-bounded) rides along with the message.
> - **v3.0.96 robust timeout param** — coerce+clamp: string "10" (crashed),
>   0/negative (instant-timeout every command), 99999 (multi-hour hang) all handled.
> 461 tests. tool_bash is now the most-hardened surface (9 fixes across 89–96).

> **Progress (2026-07-03/04) — second-model advisor, tbench-through-TUI hardening,
> Graphify (v3.0.82–3.0.92):** All bug fixes below were found by driving real
> terminal-bench-2 tasks through the actual TUI (never `-p`, never a batch judge);
> ~35 tasks, zero harness crashes, ~40% pass (failures are the local model's
> capability, not drydock). 444 tests.
> - **Second-model advisor (v3.0.82–85).** `/advisor` (url/model/key/**test**),
>   `/ask <q>` (advice to screen), **`/ask! <q>`** (inject advice into the agent's
>   context), and the `Consult` tool. Any OpenAI-compatible endpoint (e.g. Gemini);
>   no new dependency.
> - **Stall watchdog (v3.0.86).** The activity line warns "no output for Ns" (180s)
>   when the model server hangs mid-generation. Advisory only. Validated live in
>   BOTH directions — quiet on a legit 12-min/3.9k-token generation, fired on a
>   1.0k-token frozen stall.
> - **Compaction on the REAL token count (v3.0.87).** `maybe_compact` uses
>   `max(char-estimate, server's last prompt tokens)` — the chars/3 estimate
>   undercounts token-dense build/code output, so compaction had fired late.
> - **ViewImage-over-OCR (v3.0.88).** Tool desc + system prompts steer the model to
>   its own vision (not tesseract) for reading invoices/scans/screenshots.
> - **`tool_bash` hardened (v3.0.89–91), 3 fixes:** background processes (`cmd &`
>   no longer hangs to timeout + kills the server; shell-exited-but-pipe-held →
>   return without killing — validated on pypi-server + a bg QEMU VM);
>   `stdin=DEVNULL` (no TUI-terminal inheritance / stdin hangs); run under **bash
>   not dash** (`[[ ]]`, `<<<`, arrays, `{1..n}`, `<(…)` now work). Confirmed nested
>   PTYs (pexpect) still work under all three (interactive-TTY tasks unaffected).
> - **Graphify MCP integration (v3.0.92).** drydock's existing MCP client connects
>   to [Graphify](https://github.com/safishamsi/graphify)'s knowledge-graph stdio
>   server with NO code change (agent called `mcp__graphify__god_nodes` live).
>   Documented as `docs/graphify.md` + `examples/mcp/graphify.json`; fully-local
>   build path. Complements the built-in `/graphrag`.

> **Progress (2026-06-29) — multimodal, context diagnostics, POA&M, hardening
> (v3.0.72–3.0.81):**
> - **Multimodal/vision, first-class.** Reference an image path → it's attached
>   for a vision model (📎 confirmation); the agent can also call **`ViewImage`**
>   to look at images it finds itself (v3.0.77–78). `Read` on an image points to
>   `ViewImage` (v3.0.80); a server image-decode 400 degrades to a clean message
>   (v3.0.81). Verified live on the mmproj gemma server.
> - **`/context` + server probe (v3.0.72/76).** View/set & persist the context
>   budget, and probe the model server's real `n_ctx` (llama.cpp `/props`, vLLM
>   `max_model_len`) — tells you whether a "stuck at 32k" cap is your config or a
>   smaller-context server. (No 32k is hardcoded; default is 65536.)
> - **GraphRAG ingests `.ckl`/`.cklb`** (v3.0.72) — checklists become queryable.
> - **CCI→NIST control auto-map (v3.0.74)** — `/stig graph` links each STIG rule
>   to its 800-53 control via DISA's CCI list (3551 CCIs, cached, offline-safe).
> - **eMASS POA&M CSV (v3.0.79)** — `/stig poam` exports open findings to a
>   deterministic eMASS-headered CSV (Control via CCI, CAT→High/Moderate/Low,
>   Status=Ongoing, Milestone=Fix Text). Stdlib-only.
> - **Sub-agent summary cap (v3.0.75)** — Dispatch/task return a bounded partition
>   so a sub-agent's work can't bloat the main context.
> - **Teardown de-flake (v3.0.72)** — fixed a real `_refresh_status` shutdown race
>   (the long-standing flaky test; 0/20 after the fix).

> **Progress (2026-06-28, late) — knowledge ingestion + RMF automation
> (v3.0.57–3.0.63):**
> - **Self-documenting:** the system prompt now teaches the model Drydock's own
>   slash commands, so a user can just ask "how do I add my docs / make a skill?"
>   and the model answers. Commands documented on README + website + PyPI.
> - **GraphRAG ingestion UX:** `/graphrag add <path>` (incremental),
>   `/graphrag query <q>` (test retrieval), `status` lists sources.
> - **PDF + Word ingestion (v3.0.62):** `drydock/extract.py` — `.docx` via stdlib
>   (zip/XML), `.pdf` via `pdftotext` (poppler) or optional `pip install
>   drydock-cli[pdf]` (pypdf). So SSPs/POA&Ms in PDF/Word ingest directly.
> - **Author skills in-TUI (v3.0.61):** `/skills new <name> <prompt>`; built-in
>   skills now ship in the wheel (`drydock/builtin_skills/`).
> - **🎖️ RMF Automation (v3.0.63) — Operation RMF Automata, Phase 1:** ingest
>   the NIST SP 800-53 Rev 5 OSCAL catalog into the GraphRAG KB (`/rmf bootstrap
>   [families]`, `drydock/rmf.py`) so controls are queryable offline; plus four
>   bundled RMF skills — `/rmf-control`, `/rmf-categorize`, `/rmf-review`,
>   `/rmf-poam` — that drive the `Knowledge` tool over the catalog + the user's
>   own ingested SSP/POA&M/scan artifacts. Control-ID lookup works (added a
>   code-token entity pattern, `AC-2`/`SI-4`). 100% local-first for CUI. See §11.
> - **🎖️ RMF Phase 2 (v3.0.64) — typed ontology graph SHIPPED:** a stdlib
>   in-memory typed graph (`drydock/rmf_graph.py`, NOT Neo4j) — Control /
>   Objective / Component / Vulnerability / Boundary nodes; ASSESSES / IMPLEMENTS
>   / RESIDES_ON / AFFECTS edges. `/rmf bootstrap` builds the Control+Objective
>   backbone from OSCAL; the agent records topology with the `GraphAdd` tool and
>   traces relationships with `GraphQuery` — including **control inheritance**
>   ("which servers inherit physical controls"), verified live in the TUI.
>   Phase 3 (LLM relationship extraction at scale) + STIG automation (§11.1) remain.

> **Progress (2026-06-28) — agentic-harness feature push (v3.0.45–3.0.56):**
> Drydock grew from a core file/bash agent into a full agentic CLI harness.
> Every capability is clean-room / stdlib-only (no new runtime deps beyond
> `openai` + `textual`), unit-tested, and **verified hands-on in the real TUI**.
> - **Publishing is LIVE again.** `drydock-cli` is on an active PyPI account
>   (the earlier "blocked on reinstatement" status is resolved); v3.0.45→3.0.56
>   all published. GitHub auto-auths via a credential helper.
> - **Internet search** — `WebSearch` / `WebFetch` tools (DuckDuckGo, stdlib,
>   offline-safe).
> - **GraphRAG knowledge base** — users build a local entity-graph index from
>   their docs/code (`/graphrag build`); the agent retrieves via the read-only
>   `Knowledge` tool.
> - **Version-control tools** — `GitStatus` / `GitDiff` / `GitLog` / `GitCommit`
>   (structured, truncated; commit is local + reversible, push stays gated).
> - **Multi-agent** — `Dispatch` runs up to 6 read-only sub-agents in parallel.
> - **Skills** — reusable `/<name>` commands from markdown (`~/.drydock/skills`).
> - **Loops** — `/loop <count> <prompt>` for iterative runs (Esc stops).
> - **MCP** — clean-room JSON-RPC stdio client; connects to configured MCP
>   servers and exposes their tools as `mcp__<server>__<tool>`.
> - **Semantic chunking** — `Read` on a >1500-line file returns a structure
>   index (symbols + line numbers) instead of dumping it.
> - Plus: `/compact`, configurable `context_limit` (config.toml), a runaway
>   text-repetition guard, and collapsible reasoning ("thinking") cards.
>
> **Primary model is the dense Gemma-4-31B** (QAT, 64K) — it killed the
> 26B-A4B agentic looping. Improvement is driven by **real hands-on TUI use**
> against the live model, not score-chasing (no eval harnesses — still a
> non-goal). A TUI-driven tbench launcher (`/data3/tbench_local/tui_task_lib.sh`)
> drives real tasks through the genuine TUI for bug-hunting.
> **Full resume state in `RESUME.md` (read first).**

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
- **Slash commands** for control: `/help` `/model` `/cwd` `/undo` `/back`
  `/stop` `/status` `/compact` `/graphrag` `/skills` `/loop` `/mcp` `/clear`
  `/quit`, plus any user-defined `/<skill>`.
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

The clean v3 spine: `agent.py` (loop + event stream), `providers.py`
(OpenAI-compatible client for llama.cpp), `compaction.py`, `tool_registry.py`,
`tools/` (Read/Write/Edit/Bash/Glob/Grep + the agentic toolset below), the
reliability core (§6), a Textual TUI (§5), config/onboarding, and the
scanner-gated release pipeline.

**Agentic toolset modules (all clean-room, stdlib-only):** `web.py`
(WebSearch/WebFetch), `gittools.py` (Git status/diff/log/commit), `graphrag.py`
(Knowledge KB), `skills.py` (`/<name>` commands), `mcp.py` (MCP stdio client),
plus the in-`tools` `Dispatch` (parallel sub-agents) and `task` (single
sub-agent). Tools registered: Read · Write · Edit · Bash · Glob · Grep · todo ·
task · Dispatch · GitStatus · GitDiff · GitLog · GitCommit · WebSearch ·
WebFetch · Knowledge · `mcp__<server>__<tool>` (dynamic).

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
- **M4 — Validation & launch (done):** PRD shakedown suite passes; published to
  PyPI (`drydock-cli`, account in good standing); website updated.
- **M5 — Agentic harness (done, v3.0.45–3.0.56):** internet search, GraphRAG,
  version-control tools, multi-agent Dispatch, skills, loops, MCP, semantic
  chunking — see the top progress note. All clean-room, TUI-verified.

## 10. Risks

- **PyPI access (RESOLVED).** Publishing is live again — `drydock-cli` is on an
  active account and v3.0.45→3.0.56 published cleanly. Every release is still
  gated by the provenance scanner before upload. (History: the v2 lineage was
  quarantined; v3's clean-room rebuild restored good standing.)
- **TUI scope.** A good TUI is the largest chunk; risk of over-building.
  Mitigation: ship a minimal usable transcript+cards first, iterate from real
  use.
- **Model variance.** Same prompt can pass/fail run-to-run. Mitigation:
  report both numbers; rely on advisory hardening, not brittle gates.

## 11. RMF Automation — "Operation RMF Automata"

Extends Drydock to automate Risk Management Framework (RMF) work — parsing,
mapping, and reviewing System Security Plans (SSPs), POA&Ms, and NIST SP 800-53
control baselines — while keeping CUI / sensitive architectures 100% local
(no cloud). Built on Drydock's GraphRAG knowledge base + skills.

**Phase 1 (shipped, v3.0.63):**

- **NIST 800-53 catalog ingestion** (`drydock/rmf.py`, `/rmf bootstrap
  [families]`): fetch the OSCAL Rev 5 catalog once (then offline), flatten each
  control (id, family, statement, guidance, assessment objective; OSCAL params
  resolved) into per-family docs, and ingest into the GraphRAG KB. Controls are
  queryable via the read-only `Knowledge` tool. A code-token entity pattern makes
  control-ID lookups (`AC-2`, `SI-4`, `AC-2.1`) resolve.
- **Four bundled RMF skills** (`drydock/builtin_skills/`, ship in the wheel):
  `/rmf-control <id>` (control lookup), `/rmf-categorize` (FIPS 199 + tailored
  baseline, RMF Steps 1–3), `/rmf-review <control>` (SSP implementation-statement
  reviewer vs 800-53A objectives, Steps 4–5), `/rmf-poam <finding>` (POA&M
  generator from scan/STIG findings). Each is instructed to consult `Knowledge`
  first. Users ingest their own SSPs/POA&Ms (PDF/Word/text) with `/graphrag
  build` so the skills cross-reference them.

**Phase 2 (shipped, v3.0.64):** a SCHEMA-TYPED ontology graph
(`drydock/rmf_graph.py`) — Control / Objective / Component / Vulnerability /
Boundary nodes; `ASSESSES`, `IMPLEMENTS`, `RESIDES_ON`, `AFFECTS`, `ENHANCES`
edges. A stdlib in-memory graph persisted as JSON (`<cwd>/.drydock/rmf/graph.json`)
— NOT Neo4j, to stay clean-room and local-first. `/rmf bootstrap` builds the
Control + Objective backbone from the OSCAL catalog. Two tools: **`GraphAdd`**
(the agent records components / implements / resides_on / vulnerabilities as it
reads SSPs & scans) and **`GraphQuery`** (control + its objectives + implementers;
family; component; implementers; **inherited** — controls a component inherits
from its parent system via `RESIDES_ON`→`IMPLEMENTS`). Inheritance reasoning
("which servers inherit physical security controls") verified live in the TUI.

**Phase 3 (planned):** automatic LLM relationship extraction at scale (populate
Component/Vulnerability nodes + edges by reading SSPs/scans without hand-asserted
GraphAdd calls), failed-inherited-control propagation, and richer graph paths.

**Constraints:** local-first inference for CUI; runs alongside the primary
reasoning model on the reference multi-GPU rig; advisory-not-blocking; clean
provenance (the OSCAL catalog is U.S. public domain).

### 11.1 STIG automation & checklist files (`.ckl` / `.cklb`)

Targets the most tedious part of Implement/Assess: DISA STIGs and the `.ckl`
(XML) / `.cklb` (JSON) checklist files used by STIG Viewer and eMASS. These files
carry hostnames, IPs, and statuses that are almost always CUI — so parsing stays
entirely local.

- **XCCDF→.ckl generator SHIPPED (v3.0.70):** `/stig new <benchmark-xccdf.xml>`
  parses a raw DISA STIG XCCDF benchmark (e.g. the Application STIG) and generates
  a blank `.ckl` (all rules Not_Reviewed) — the inverse of STIG Viewer. Closes the
  loop: STIG benchmark → blank .ckl → assess (pull in app evidence) → completed .ckl.
  Validated against the full real DISA Application Security & Development STIG (U_ASD_STIG_V6R1, 286 rules → 1.1MB valid .ckl in ~0.02s) + a vSphere STIG.
- **Engine SHIPPED (v3.0.65):** `drydock/stig.py` parses `.ckl` (XML) and `.cklb`
  (JSON) into a compact per-rule model and regenerates them with **edit-in-place
  fidelity** (only status/finding-details/comments change; all other STIG_DATA is
  preserved, so STIG Viewer/eMASS re-import cleanly). Tools `StigRules` (list +
  status counts), `StigRule` (one rule's check/fix text), `StigSet` (record
  status + narrative, save). Verified live: the model read a rule, assessed it
  against `sshd_config` evidence, and set it Open with a justification.
- **§3.5 STIG Automated Assessor & `.ckl` generator (assessor skill + loop, planned):** parse
  `.ckl`/`.cklb` into a compact internal model (rule id, severity CAT I/II/III,
  check content, fix text, status, finding details); the LLM evaluates each
  check against supplied technical evidence (config files, command output) and
  sets **Not a Finding / Open / Not Applicable** with a narrative justification;
  rebuild a well-formed `.ckl`/`.cklb` that re-imports cleanly.
- **§3.6 Automated Remediation Scripter SHIPPED (v3.0.69):** bundled
  `/stig-remediate <ckl> <rule>` skill — reads the rule's Fix Text (StigRule),
  infers the OS, and writes an idempotent Bash/PowerShell/Ansible remediation
  script (it never runs it — the operator reviews). Verified live: produced a
  set-`PermitRootLogin no` Bash script for an Open SSH finding.
- **Ontology additions SHIPPED (v3.0.69):** `STIG` (name/version, `APPLIES_TO`
  Component), `STIG-Rule` (rule_id, severity, status, CCI; `PART_OF` STIG,
  `EVALUATES` Component), and `Control —SATISFIED_BY→ STIG-Rule`. `/stig graph
  <ckl>` ingests a checklist into the typed graph; `/stig graph` now AUTO-links each rule to its NIST control via the DISA CCI
  map (CCI→800-53, built from U_CCI_List.xml = 3551 CCIs, fetched once + cached,
  offline-safe); `GraphAdd satisfies` remains for manual links. (v3.0.74)

**Design decision (context efficiency):** the harness does NOT feed raw checklist
XML to the model. It parses the checklist **once** into a lightweight JSON model,
evaluates **one check at a time** (composes with `/loop`) so each turn sees only
that rule's check-content + the relevant evidence (forcing step-by-step reasoning
before a status), writes the result back into the model, and **rebuilds** the
`.ckl`/`.cklb` from the model at the end. Parse→per-check→rebuild keeps context
tiny (vital on a 64K local model) and keeps XML generation schema-correct and
decoupled from evaluation.

## 12. E2E testing (connected configuration)

The connected E2E test framework validates the RMF/STIG pipelines with network
access for live regulatory feeds — but **scoped to DETERMINISTIC + integration
testing only**. Operator decision (2026-06-28): model assessment & remediation
ACCURACY stays verified **hands-on in the real TUI**, one case at a time — NOT an
automated model-eval harness (the no-eval-harness / TUI-only rules stand).

**Built (deterministic, `tests/test_e2e_connected.py` + `test_stig.py`):**
- Network-resilience fallback: `rmf.bootstrap(refresh=True)` pulls upstream but
  falls back to the cached catalog on any failure; offline-no-cache raises.
- Live NIST OSCAL fetch + parse — opt-in (`DRYDOCK_E2E_NETWORK=1`), kept out of
  the fast suite.
- `.ckl` regenerates well-formed with the DISA status enum + `STIG_DATA` fidelity
  preserved; malformed / incomplete / empty checklist stubs degrade gracefully.
- Graph relationship semantics (`COMPONENT —IMPLEMENTS→ CONTROL`).

**Out of scope here (TUI-driven instead):** STIG assessment correctness, the
remediation→Docker→re-assess accuracy loop, and reasoning-trace grounding —
because on the local model these measure model judgment, which is verified by
driving the real TUI (e.g. `/loop N /stig-assess <ckl>` and inspecting results).
Docker remediation *scaffolding* with a DETERMINISTIC end-state assertion (did
the config actually change / does the `.ckl` validate) is admissible if built.
