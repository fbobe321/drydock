# Drydock v3 — Session Resume / Handoff

The work runs on the workstation; you steer it remotely via Claude Code's
Remote Control.

- **Resume the actual conversation (full context):** on the workstation,
  `cd /data3/drydock-v3 && claude --resume "drydock"` (or `claude --continue`).
  History lives in `~/.claude/projects/…` and comes back intact.
- **Control it from a phone/laptop:** enable once via `claude` → `/config` →
  "Enable Remote Control for all sessions" → true; then a resumed session shows
  up at claude.ai/code (or the Claude app → Code). Execution stays on this box;
  the remote device is just the UI. Needs a claude.ai subscription + `/login`
  (API keys don't work); the box must stay on with the `claude` process running.

- **Keep it alive untended — run it in `tmux`** so closing the terminal or an
  SSH drop doesn't kill the `claude` process:
  ```bash
  tmux new -s dd                                   # start a named session
  cd /data3/drydock-v3 && claude --resume "drydock"   # remote control auto-on if enabled
  # detach (leave it running):  Ctrl-b then d
  # later, reattach from an SSH login:  tmux attach -t dd
  ```
  Detaching keeps `claude` running, so the phone/web link stays live. Only an
  actual reboot or `tmux kill-session` stops it — and even then your code is
  committed/pushed and the conversation is resumable, so nothing is lost.

> **Nothing here is lost by stopping.** Code = committed + pushed to the private
> repo. Conversation = saved under `~/.claude/projects/…`, replayed by
> `claude --resume`. Only the live remote-control *link* needs the process up;
> restart `claude --resume` to get a fresh one.

This file is the **fallback primer** for starting a brand-new session without
that history — paste it as the first message.

---

## What this is

Drydock v3 is a **clean-room, Apache-2.0, provider-agnostic terminal coding
agent** for local LLMs. It is NOT a fork — it replaces the v2 line (a
`mistral-vibe` fork whose inherited phone-home code got the PyPI account
quarantined). The whole point of v3 is clean IP provenance owned end to end.

- **Repo:** `https://github.com/fbobe321/drydock-v3` (PRIVATE), branch `master`.
- **Checkout:** `/data3/drydock-v3` on the workstation.
- **Primary model (UPDATED 2026-06-22):** **Gemma-4-31B dense** (QAT
  `Q4_K_XL`, NOT the 26B-A4B MoE) served by llama.cpp at
  `http://localhost:8000/v1` (model name `gemma4`). Swapped because the
  26B-A4B's ~4B active params caused fatal agentic loops (180× identical
  pytest). The dense 31B is loop-free; ~3–4× slower (15 tok/s tensor-split
  vs 64) but it FINISHES. Vision via matching `mmproj-gemma4-31b-F16.gguf`.

---
## ⭐ 2026-07-06 — Bash I/O-path hardening via direct probing (v3.0.93 → 3.0.96)

**Key insight this session:** *directly probing drydock's own I/O paths* found 4
real bugs that grinding hard tbench tasks did NOT — the hard tasks (cobol, db-wal,
etc.) run the toolchain cleanly and are model-limited, so they don't surface
harness bugs; the leverage is in probing edge cases of `tool_bash`, Read, etc.
All 4 fixes are in `tool_bash` output/param handling:
- **v3.0.93** binary/non-UTF8 output no longer crashes the reader thread →
  "(no output)"; now `errors="replace"` (text survives).
- **v3.0.94** `_sanitize_bash_output()` strips ANSI escapes + drops NUL bytes.
- **v3.0.95** partial output preserved when a command times out (tail-bounded).
- **v3.0.96** `_coerce_timeout()` — string/0/negative/absurd timeout params fixed.
`tool_bash` is now the most-hardened surface (fixes 89–96). To keep hunting: keep
probing I/O edges (Read on binary keeps NUL; cwd/`cd` persistence; STOP discards
partial output — all candidates), not more hard-reasoning tbench tasks.

## ⭐ 2026-07-03 — second-model advisor + 7 tbench-through-TUI fixes + Graphify (v3.0.82 → 3.0.92)

**Graphify MCP integration (v3.0.92).** [Graphify](https://github.com/safishamsi/graphify)
(external, MIT) serves a codebase **knowledge graph** over an MCP stdio server;
drydock's existing MCP client connects with **no code changes** — validated
end-to-end through the TUI (agent autonomously called `mcp__graphify__god_nodes`).
Made permanent as docs+example, NOT code coupling: `docs/graphify.md`,
`examples/mcp/graphify.json`, README + website notes, `tests/test_graphify_example.py`.
Fully local: structural build needs no LLM; semantic build points `OPENAI_BASE_URL`
at the local gemma server. Complements drydock's own `/graphrag` ([[project_graphrag_feature]]).


PyPI **3.0.91**; HEAD pushed/green; **441 tests**. Everything below was found by
driving **real terminal-bench-2 tasks through the actual TUI** (the
`/data3/tbench_local/tui_task_lib.sh` harness: `ddt_up`/`ddt_tui`/`ddt_verify`/
`ddt_down`, gemma at `host.docker.internal:8000`) and fixing what broke — never
`-p`, never a batch judge. ~35 tasks across every category; harness had **zero
crashes**, ~40% pass (failures are the local model's capability, not drydock).

**Second-model advisor (v3.0.82–85).** Ask a stronger model (e.g. Gemini via its
OpenAI-compatible endpoint) for help without leaving the TUI. `Consult` tool +
`/advisor` (url/model/key/**test**) + `/ask <q>` (advice to screen) + **`/ask! <q>`**
(inject the advice straight into the agent's context). Config keys
`advisor_base_url`/`advisor_model`/`advisor_api_key`. No new dep (reuses `openai`).

**The 7 fixes (each a real drydock bug hands-on use surfaced, unit tests missed):**
1. **v3.0.84 `/advisor test` timeout** — 30s false-negative on a cold/slow model;
   now 90s + "REACHABLE but slow". (Found testing two live LLMs on .21/.22.)
2. **v3.0.86 stall watchdog** — the model server (llama.cpp) hangs mid-generation
   on hard prompts (token count freezes, elapsed climbs); the activity line now
   warns "no output for Ns" after 180s. Advisory only (never kills). Fired
   correctly ~8× live; silent on legit slow steps.
3. **v3.0.87 compaction on the REAL token count** — `maybe_compact` keyed on the
   chars/3.0 estimate, which undercounts token-dense build/code output, so
   compaction fired late (gauge showed 64% while estimate read ~55%). Now uses
   `max(estimate, state.last_input_tokens)` — the server's real prompt-token
   count. (Surfaced by build-cython-ext.)
4. **v3.0.88 ViewImage-over-OCR** — the model reached for tesseract/pdftotext on
   invoice/scanned images instead of its own vision; tool desc + both system
   prompts now say use ViewImage first. (Surfaced by financial-document-processor.)
5. **v3.0.89 background processes** — a naive `python server.py &` (no redirect)
   HUNG `tool_bash` to the timeout then killed the server (the backgrounded child
   inherits the stdout pipe → drain thread never sees EOF). Now: shell exited but
   pipe still held → return promptly WITHOUT killing, so the bg server survives.
   Validated live on pypi-server (`pypiserver … & sleep`) + qemu-startup (bg VM).
   (Surfaced by kv-store-grpc.)
6. **v3.0.90 stdin=DEVNULL** — `tool_bash` never set stdin, so a subprocess
   inherited the TUI's terminal; a stdin-reading command would steal keystrokes or
   hang. Now DEVNULL → instant EOF, isolated from the TUI; piped/redirect input
   still overrides it. (Found investigating interactive-stdin tasks.)
7. **v3.0.91 bash-not-dash** — `shell=True` used `/bin/sh` = dash on Debian, so
   `[[ ]]`, `<<<`, arrays, `{1..n}`, `<(…)` failed with confusing syntax errors
   the model looped on. Now resolves bash once (`_detect_bash`) and passes
   `executable=_BASH_SHELL`; falls back to /bin/sh if bash is absent.

**tbench passes worth noting:** build-pmars, build-pov-ray (render matches ref),
modernize-scientific-stack, hf-model-inference (bg Flask+HF model),
pypi-server (bg pypiserver), qemu-startup (bg Alpine VM on telnet:6665),
constraints-scheduling. Fixes 89–91 all harden `tool_bash` (server/daemon +
interactive-stdin + shell categories). Launcher gained `uv --refresh` (publish→run
PyPI propagation lag). `_BASH_SHELL`/`_detect_bash`, stall watchdog, and the bg/stdin
logic all live in `drydock/tools/__init__.py`; compaction in `drydock/compaction.py`.

---
## ⭐ 2026-06-29 (overnight) — multimodal + context diagnostics + hardening (v3.0.75 → 3.0.81)

PyPI **3.0.81**; HEAD pushed/green; website redeployed. **409 tests.**

- **Multimodal is first-class.** v3.0.77 user-attached vision (📎 confirmation +
  the model is told it has vision); v3.0.78 **agent-side `ViewImage` tool** — the
  agent can look at images it discovers (image rides back on the tool result;
  the llama.cpp server reads images from tool-role messages, verified); v3.0.80
  `Read` on an image points to `ViewImage`; v3.0.81 a server image-decode 400
  degrades to a clean message (found via TUI edge-case hunt). All TUI-verified on
  the mmproj gemma server (read "DRYDOCK 42" / "42" off PNGs).
- **`/context` server probe (v3.0.76).** Probes the model server's real `n_ctx`
  (llama.cpp `/props`, vLLM `max_model_len`) and warns if the SERVER is smaller
  than drydock's budget — the definitive "stuck at 32k" diagnostic. Verified
  against a fake 32k server: it warns correctly. (See [[feedback_context_limit_32k_trap]].)
- **eMASS POA&M CSV (v3.0.79).** `/stig poam <ckl>` → deterministic CSV of open
  findings (Control via CCI map, CAT→High/Moderate/Low, Status=Ongoing,
  Milestone=Fix Text). `drydock/poam.py`, stdlib-only.
- **Sub-agent summary cap (v3.0.75).** Dispatch/task returns are capped (~4000
  chars) so a sub-agent's work can't bloat the main context.
- **Overnight TUI bug-hunt (all clean except the corrupt-image fix above):** huge
  bash output stays ~5% ctx; git tools, Dispatch fan-out (bounded summaries),
  full 286-rule STIG chain (new→summary→graph, 265/286 CCI-linked), and a
  multi-turn build (wrote + self-verified a Stack module) all held up.
- 🚨 **Eval-harness ban REAFFIRMED 2026-06-29.** The `EVAL_HARNESS_DESIGN.md` +
  `_anthropic/INTERP_AGENT_EVAL_INTEGRATION.md` specs exist but the operator said
  **keep the ban** — do NOT build `drydock/eval/`. See [[feedback_no_custom_eval_harness]].

---
## ⭐ 2026-06-29 — STIG pipeline completion + user-reported fixes (v3.0.62 → 3.0.74)

Repo HEAD pushed + green; **PyPI 3.0.74**; website **deployed** to production
(drydock.pages.dev + www.drydock-cli.com). Test suite **389 passing**.

**RMF/STIG program — COMPLETE end to end (raw benchmark → completed checklist):**
- **`/stig new <xccdf>`** (v3.0.70) — parses a raw DISA STIG **XCCDF benchmark**
  into a blank `.ckl` (the missing first arrow). Validated against the full
  **286-rule Application STIG** (U_ASD_STIG_V6R1) — 1.1MB valid `.ckl` in ~0.02s.
- **`/stig graph` CCI auto-mapping** (v3.0.74) — `drydock/cci.py` builds a
  CCI→800-53 map from DISA's `U_CCI_List.xml` (3551 CCIs, cached, offline-safe);
  `ingest_checklist` auto-creates `Control —SATISFIED_BY→ STIG-Rule` edges. Was a
  documented follow-on; now shipped. Closes RMF/STIG task #21's last gap.
- STIG ontology nodes + `/stig-remediate` (v3.0.69), assessor + engine (3.0.65-66).
- **`/stig` summary** (v3.0.73) — exact `/loop N` suggestion, scale hint for big
  checklists, no silent 50-cap truncation. Logic in `stig.summary_lines()` (tested).

**User-reported fixes (operator was at work):**
- **`/context [n]`** (v3.0.72) — view/set + PERSIST the context-window budget.
  Fixes the "stuck at 32768" trap: a stale `context_limit` in config.toml (drydock
  never rewrites an existing config) or a smaller server `-c`. NO 32k hardcode —
  default is 65536. See [[feedback_context_limit_32k_trap]].
- **GraphRAG ingests `.ckl`/`.cklb`** (v3.0.72) — checklists were silently
  skipped; now flattened to per-rule findings (extract.py). Verified end-to-end:
  the agent calls `Knowledge` and answers "which findings are open" from a `.ckl`.

**Real bugs found via hands-on TUI testing (the discipline pays off):**
- **Teardown crash** (v3.0.72) — the 0.18s `_tick_work` timer could fire during
  shutdown and `query_one("#status")` after widgets were gone → NoMatches/
  ScreenStackError crashed the app. `_refresh_status` now guards both. This was
  the root of the long-standing flaky TUI test (0/20 flake runs after the fix).
- **CCI fetch mkdir bug** — `cci.load_map` wrote to `.drydock/rmf/` before the dir
  existed → silent offline fallback. Mocked unit tests missed it; the live TUI run
  caught it. Fixed + regression test.

**Also:** broadened deterministic test coverage (web/mcp/rmf_graph/stig/cci edge
cases); repo hygiene (`.gitignore` .wrangler/.drydock/checklists); README +
website fully updated for the STIG suite + `/context`. Website deploy how-to:
[[reference_website_deploy]].

---
## ⭐ 2026-06-28 (cont.) — doc ingestion UX + skill authoring + self-documenting prompt

- **v3.0.60** — GraphRAG doc ingestion UX: `/graphrag add <path>` (incremental),
  `/graphrag query <q>` (test retrieval), `/graphrag status` lists sources.
  `graphrag.add_to_index()`/`sources()`.
- **v3.0.61** — `/skills new <name> <prompt>` authors a skill from the TUI
  (`skills.create_skill`, $ARGS supported); the **system prompt now documents
  Drydock's own slash commands** (`tuning._DRYDOCK_COMMANDS_HELP`) so the MODEL
  answers "how do I add docs / make a skill?" itself. Slash commands documented
  on README (table + sections), website, PyPI. All verified hands-on in the TUI.

## ⭐ 2026-06-28 — Docs + website updated for the new capabilities

- **v3.0.57** — README + `docs/PRD.md` rewritten for the agentic harness
  (Capabilities section, full tool/slash-command lists, dense-31B model, Python
  3.11+, corrected the stale "PyPI blocked" language). Published so the PyPI
  long-description updates too.
- **Website is now in the CLEAN repo** at `web/index.html` (fresh, clean-room —
  NOT from the radioactive old fork's `web/`). Live at **drydock.pages.dev** +
  **www.drydock-cli.com** (Cloudflare Pages project `drydock`, direct-upload).
  **Redeploy:** `web/` is the source; the box's `/usr/bin/node` is v22 (nvm's is
  v18, too old for wrangler), so:
  ```
  export CLOUDFLARE_API_TOKEN=$(cat ~/.config/drydock/cloudflare_token)
  export CLOUDFLARE_ACCOUNT_ID=$(cat ~/.config/drydock/cloudflare_account_id)
  PATH=/usr/bin:$PATH /usr/bin/npx --yes wrangler@latest pages deploy web \
    --project-name drydock --branch main --commit-dirty=true
  ```

## ⭐ 2026-06-27 — SHIPPED: agentic-harness feature push (3.0.50–3.0.56)

Built out the PRD "Drydock Agentic CLI Orchestration" capabilities. All on
GitHub + PyPI, each verified hands-on in the real TUI. **Tool registry now:**
Read · Write · Edit · Bash · Glob · Grep · todo · task · **Dispatch** ·
**GitStatus/GitDiff/GitLog/GitCommit** · **WebSearch/WebFetch** · **Knowledge** ·
**mcp__\<server\>__\<tool\>** (dynamic).

- **3.0.50 internet search** — `WebSearch`/`WebFetch` (`drydock/web.py`, stdlib
  DuckDuckGo POST + page-to-text; offline-safe). Model chained search→fetch live.
- **3.0.51 Version Control tools** — `GitStatus/GitDiff/GitLog/GitCommit`
  (`drydock/gittools.py`); structured + truncated; commit is local/reversible,
  push stays gated. Model chained status→diff→commit.
- **3.0.52 Skills** — `drydock/skills.py`; markdown skills in
  ~/.drydock/skills + <proj>/.drydock/skills, invoked `/<name>` ($ARGS subst);
  `/skills` lists them.
- **3.0.53 /loop** — `/loop <count> <prompt>` repeats a prompt (Esc stops).
  NOTE: loop state is `self._repeat` (NOT `self._loop` — collides with Textual
  App._loop).
- **3.0.54 multi-agent `Dispatch`** — runs up to 6 read-only sub-agents in
  parallel (shared `_run_subagent`; isolated _abort each). Serializes on the
  single-slot local server; parallel on multi-slot/backends.
- **3.0.55 MCP** — `drydock/mcp.py` clean-room JSON-RPC stdio client; config
  ~/.drydock/mcp.json ("mcpServers"); tools registered as mcp__server__tool at
  startup (crash-proof); `/mcp` lists them. Mock server in tests/fixtures.
- **3.0.56 semantic chunking** — `Read` on a >1500-line file with no window
  returns a STRUCTURE INDEX (def/class/header anchors) instead of dumping it.

PRD gap-analysis verdict: File System / Execution / Agent State were already
complete; the from-scratch items were **Version Control tools** + **semantic
chunking** — both now done.

---
## ⭐ 2026-06-26 (PM) — SHIPPED: PyPI live, TUI-driven launcher, GraphRAG, context fix

**Newest first (all pushed + on PyPI 3.0.49):**
- **v3.0.49 GraphRAG knowledge base** — users build a local entity-graph index
  from their docs/code; the agent retrieves from it via the read-only
  `Knowledge` tool. `drydock/graphrag.py` (clean-room, stdlib only — no
  embeddings/deps): chunk → entity extraction → co-occurrence graph → query with
  1-hop expansion. `/graphrag build <path>|status|clear` (TUI + CLI). Verified
  hands-on: model called Knowledge and answered KB-only facts. Tests:
  tests/test_graphrag.py.
- **v3.0.48 configurable `context_limit`** — was HARDCODED 65536 in cli.py (even
  clobbered `**cfg`), so a 32k-server user OOM'd before compaction fired and
  /compact found nothing. Now a config.toml setting (DEFAULTS < file <
  `--context-limit`); set it to your server's -c. Manual /compact also escalates
  to emergency_compact when a normal pass leaves history >50% full.
- **v3.0.47 thinking-visibility** (operator's feature, finished + rendered):
  `extract_thinking` + `ReasoningChunk` → collapsed `ReasoningCard` in the TUI.
- **v3.0.47 runaway text-repetition guard** (see below).



**Done this session (all pushed; HEAD past b6cedd7):**
- **`/compact` implemented FRESH** (TUI + CLI) — was advertised in `/help`, never
  wired. v3.0.46. Honesty fix: the ctx gauge only moves on a real shrink (found
  via hands-on tmux). Regression tests in `tests/test_compact_command.py`.
- **PyPI publishing is LIVE again** — `drydock-cli` 3.0.32 → **3.0.47 published**.
  NOT banned; active account token at `~/.config/drydock/pypi_token` (old
  quarantined account is the `.bak`). See Credentials section for the exact
  publish command. Earlier "blocked on reinstatement" notes were WRONG.
- **v3.0.47 — runaway text-repetition guard** (RESUME Task #41, half of it):
  `loop_detect.runaway_repetition_len()` + a throttled check in
  `providers.stream()`. When the model collapses into repeating one short unit
  (the `295:`×1365 failure), it trims the repeated tail, shows
  "[stopped — output began repeating]", and stops reading. Advisory, never
  raises. Conservative thresholds so legit repetition never trips it; 11 tests
  + hands-on TUI verified normal streaming is unaffected. (The other half —
  server-side `--repeat-penalty` — remains the operator's call.)
- **GitHub re-synced + auto-auth.** Local was 26 commits ahead (no cached creds →
  silent push failures). Pushed all; added a git credential helper reading
  `~/.config/drydock/github_token`, so `git push` now just works.
- **🎯 PRIMARY GOAL MET — TUI-driven tbench launcher.** `/data3/tbench_local/
  tui_task_lib.sh`: `source` it, then `ddt_up <task>` (builds the task's docker
  image, installs `drydock-cli` from PyPI inside, points it at gemma4 via
  host.docker.internal) → `ddt_tui <task>` (launches the REAL TUI in a tmux
  session `ddt_<task>` via `docker exec -it`) → drive by hand with
  `tmux send-keys`/`capture-pane` → `ddt_verify <task>` (runs the task's OWN
  `tests/test.sh`, prints reward 1/0) → `ddt_down <task>`. No `-p`, no judge
  pipeline — env-setup + the real verifier, hand-driven. This is THE path.

**Hands-on TUI baseline (3 tbench-2 tasks through the real TUI):**
- `fix-git` → **reward 1** (found lost commit via reflog, merged, resolved
  conflict). `nginx-request-logging` → **reward 1** (8/8 tests; the **sudo
  approval modal fired and `a`=Always worked** — a TUI-only path `-p` never hits).
  `sqlite-db-truncate` → not completed (model-slowness, see below), but used to
  stress-probe the TUI.
- **No drydock bugs found.** Validated: clean tool cards + Plan panel, no
  tool-call-as-text leaks, **binary tool output (`print(open(...,'rb').read())`)
  does NOT corrupt the transcript**, **Esc cleanly interrupts a long non-stream
  turn** and the session stays responsive after, empty-response path nudges once
  then breaks (no spin — code + observed), context gauge accurate, `/compact`
  works. The from-scratch TUI is solid.
- **Only real limiter = model speed**, exactly as the prior caveat said: dense
  31B at ~15 tok/s with `--reasoning-budget 20000` ⇒ a single hard-task turn can
  run 11+ min (sqlite recovery hit this). NOT a loop, NOT a drydock bug — the
  server even cancels at its default 600s timeout. Operator lever unchanged:
  lower `--reasoning-budget` in `/data3/Models/start_gemma4_31b_llamacpp.sh`.
  Note: during non-streaming tool turns the working-line token count holds at the
  session total by design (elapsed timer still advances to show liveness).

---
## ⭐ 2026-06-26 — LATEST STATE (READ THIS FIRST)

**Direction locked by the operator (2026-06-26):**
- **v3 (`/data3/drydock-v3`) is THE codebase.** The old `/data3/drydock`
  mistral-vibe fork is DEAD and RADIOACTIVE — do **NOT** read it, run it, or
  copy/port anything out of it. It is the inherited lineage/phone-home code that
  got the PyPI account banned. v3 stays **from-scratch / clean-room**. Missing
  features get **built fresh in v3**, never ported.
- **Improve drydock by USING its real TUI, hands-on (tmux).** `-p`,
  programmatic, pexpect, and stress harnesses do **NOT** count (rules #1 & #4
  below). Operator, verbatim: *"it has to go through the TUI. That is the whole
  point."*

**tbench was STOPPED (2026-06-26) — and why it matters:** the entire tbench
setup drove drydock via `drydock -p '<task>'` (one-shot). That exercises the
model + `agent.py` core but **bypasses the TUI entirely** — so the scores never
reflected the product the operator actually uses. Accordingly:
- Both harbor runs killed (`drydock_PASS3_v3.0.45_mult6_3box` + a
  `RERUN_FAILS` run). Relaunch crons PAUSED:
  `/data3/drydock/.pause_harbor_watchdog`, `.pause_tbench_chain`,
  `.pause_tbench_watchdog`.
- ~58 GB docker reclaimed (unused images + build cache); the `gemma4` server
  container/image preserved. Job result dirs (~1.3 GB) left at
  `/data3/tbench_local/jobs`.
- Last `-p` numbers (informational only, NOT a TUI measurement): pass@1 = 21/89
  (23.6%); the stopped pass@3 was ~25/89 (28.1%, partial).

**🎯 PRIMARY GOAL — TUI-driven testing (open task):** build a way to run tbench
TASKS through v3's **real TUI** (not `-p`): set up the task environment, launch
v3's TUI in tmux, feed the task prompt, let drydock work through the genuine TUI
code path, then run the task's existing verifier — verify by watching
`capture-pane`. Honor "no custom eval harness": reuse the real task defs +
verifiers and drive the genuine TUI; do not build a judge/batch-runner pipeline.

**Open task — build `/compact` FRESH in v3:** `drydock/cli.py` advertises
`/compact` in `/help` (line ~158) but `handle_command` never implements it.
Wire it to the existing `compaction.py` (`maybe_compact`/`emergency_compact`) +
the TUI, and verify hands-on in tmux. Pure from-scratch v3 work.

**⚠️ Heads-up (do NOT act on without operator OK):** the dead fork still has an
`auto_release` cron that could republish the banned-lineage package to PyPI.
Worth confirming it's disarmed — but check only the cron/flag, never the old code.

**Mistake not to repeat:** on 2026-06-25, before this decision, two fixes (a
read-timeout fix + a thinking-visibility feature) were committed into the OLD
fork `/data3/drydock` — the wrong tree. They are NOT in v3 and must NOT be
ported as code. v3 already shipped its own bash/read-timeout handling (v3.0.45,
`df51a06`). If those behaviors are wanted in v3's TUI, build them fresh.

---

## ⭐ 2026-06-24 OVERNIGHT — (history; superseded by the 2026-06-26 section above)

**Repo HEAD:** `93fbb46` (v3.0.44 + PRD update), CI **green**, all pushed. Repo PRIVATE.

**Fleet (all 4 boxes on dense 31B + vision, 64K ctx, persistent):**
- remus/Dell `.22:8000` (docker `llamacpp-gemma4-31b`, this box), romulus
  `.21:8000` (systemd), 3090 `.129:8000` (Windows `fbobe@`, watchdog
  scheduled-task **every 24h**), Jetson `.19:8080` (systemd, `/opt/models` NVMe).
- **Jetson caution:** under the 31B it *thrashes* (5 tok/s) until SSH +
  llama-server both time out, though the host stays up (17d uptime). It
  recovers when idle. Excluded from scoring runs (it converts passable tasks
  to false-timeouts). The harbor adapter now health-probes + skips dead boxes.
- Rollback to 26B-A4B: `/data3/Models/gemma4_restore_config.txt`.

**Shipped 2026-06-24 (v3.0.39→44, all CI-green + pushed):**
- v3.0.39 concrete default `base_url`; v3.0.40 Python floor 3.12→3.11
  (v2-upgrade compat); **v3.0.41 active v2→v3 config migration** (back up
  legacy/foreign config.toml, write fresh editable one — verified on the real
  v2 config on the Windows 3090).
- **v3.0.42** `-p`/one-shot surfaces unreachable LLM as the actionable message
  + exit 2 (was a raw traceback); purged `qwen` from test fixtures.
- **v3.0.43** plan panel cleared at each user-turn start (stale-plan bug, found
  by hands-on TUI use, verified live).
- **v3.0.44** `-p` trace logs tool inputs + outcomes (timeout diagnosability).
- harbor_fork adapter: **skips unreachable backends** in the rr pool; wheel
  launch helper `/data3/tbench_local/tbench_launch_lib.sh` (never-404).
- Fixed the operator's live `~/.drydock/config.toml` (qwen→gemma4 + base_url);
  de-flaked a CI-flaky TUI test; refreshed editable install → `--version` 3.0.44.

**tbench harness** (`/data3/harbor_fork`, adapter `.../installed/drydock_agent.py`).
Container networking was fixed earlier (ufw `DEFAULT_FORWARD_POLICY=ACCEPT` +
docker restart + iptables `INPUT -s 172.16.0.0/12 --dport 8899 ACCEPT`). Wheel
served by host `python -m http.server 8899` over `/data3/drydock-v3/dist/`.
- **Use the launch helper:** `source /data3/tbench_local/tbench_launch_lib.sh;
  tb_prepare_wheel 3.0.44` (builds-if-missing, verifies HTTP 200, sets
  `DRYDOCK_INSTALL_SPEC`). Pool env `DRYDOCK_BACKEND_POOL=<comma list>`; rr
  counter `/tmp/dd_backend_rr.ctr`. Eval: `result.json` →
  `stats.evals[ev].reward_stats.reward` (1.0=pass); `is_resolved` is unreliable.

**Results (dense 31B, full 89-task terminal-bench-2 corpus):**
- **pass@1 = 21/89 ≈ 23.6%** (clean 3-box, Jetson excluded). V2PASSED10 = 8/10.
- **pass@3 IN PROGRESS** (extended `--agent-timeout-multiplier 6`): job
  `drydock_PASS3_v3.0.44_mult6_3box`, 68 not-yet-passing tasks ×3 (21 pass@1
  winners carried forward in `/tmp/pass3_winners.json`), 3-box pool. Monitor
  `/tmp/pass3_progress.log`. Was ~29% climbing when the operator went to bed.
  Merge = winners ∪ retry-rescued. Self-monitoring; report the headline + which
  tasks the extended timeout rescued when it finishes (~20-40h run).

**Doom-loop verdict vs 26B-A4B (data, 219-239 trials + hands-on TUI):** the
dense 31B does NOT reproduce the 26B's hard tool-loops. Byte-identical guard
(`agent.py IDENTICAL_REPEAT_CAP=8`) fired 36/239 trials, caps at 8; ~85% never
trip it; 0 loops in ~8 hands-on TUI tasks. **One rare different failure:**
text-repetition collapse (make-mips-interpreter emitted `295:` ×1365), 1 task /
219, not seen by the tool guard. **Task #41 (queued, deferred until fleet is
free):** server-side `--repeat-penalty 1.1` (+ optional `--dry-multiplier 0.8`)
in `/data3/Models/start_gemma4_31b_llamacpp.sh`, and/or a drydock-side text-loop
guard. Server currently runs with NO repetition control.

**Open caveat:** 31B is slow on hard frontier tasks (compiler builds, ray
tracers, ARC-AGI) → genuine timeouts even at mult 6 (NOT loops — diagnosed).
Lever = lower server `--reasoning-budget` (currently 20000), operator's call.
---

## Current state (as of HEAD 956e017)

- **177 tests pass, ruff + pyright clean, release wheel builds 0 HIGH findings.**
- Local == `origin/master`, nothing uncommitted/unpushed.
- Feature-complete vs the original task list + the major v2 reliability
  hardening is ported. Backlog #1–#27 all done.
- 13 shakedowns passed (6 medium + 5 hard + 2 failure-recovery), zero
  text-form tool-call leaks.

### What's implemented
- TUI (Textual): transcript, streamed text, collapsible tool cards, multi-line
  prompt (`Ctrl+J` newline, `Enter` submits), `↑/↓` history (persists to
  `~/.drydock/history`), `Ctrl+O` expand tools.
- Slash commands: `/model /cwd /undo /back /status /clear /help /quit`.
- Config: `~/.drydock/config.toml` (defaults < file < CLI flags; NEVER modifies
  an existing file — only creates on first run). First-launch local-LLM
  autodetect (probes :8000/:11434/:1234).
- Safety: catastrophic-command denylist (`rm -rf /`, `mkfs`, fork bombs — hard
  refused) + approval modal for sensitive commands (`sudo`, installs, network,
  `git push` → Allow/Always/Deny).
- Guards (advisory, never block): syntax, main-entry, stub-only, missing
  sibling imports, bare-raise; conflict-marker Write/Edit refusal.
- Gemma reliability: non-streaming on tool turns, thinking/special-token
  stripping, hallucinated-tool redirect, text-form tool-call recovery + retry,
  adaptive `reasoning_effort` (high-to-plan / low-to-continue), loop nudges +
  same-path write-thrash advisory, blank/dir-path guard, two-tier compaction.

### Module map (`drydock/`)
`agent.py` (loop, adaptive reasoning, hallucinated-tool redirect, drop_last_turn) ·
`providers.py` (OpenAI-compatible, non-streaming for Gemma tools, unreachable
error) · `bash_safety.py` (denylist + approval tier) · `guards.py` (advisory
write guards + conflict markers) · `loop_detect.py` · `tuning.py` (Gemma tuning) ·
`compaction.py` · `config.py` · `detect.py` · `tools/__init__.py` (Read/Write/
Edit/Bash/Glob/Grep + undo journal) · `tui/{app,widgets,approval,messages}.py`.

## How to run / test / verify

On the **workstation** (this is where it runs — Python at
`/home/bobef/miniconda3/bin/python3`, has textual/openai):

```bash
cd /data3/drydock-v3
# Run the TUI (in a project dir; model on local :8000)
PYTHONPATH=/data3/drydock-v3 /home/bobef/miniconda3/bin/python3 -m drydock --model gemma4 --provider vllm
# Tests — the local pytest_cov plugin is broken, so disable it:
/home/bobef/miniconda3/bin/python3 -m pytest tests/ -q -o addopts= -p no:cov -p no:cacheprovider   # 177 pass
/home/bobef/miniconda3/bin/python3 -m ruff check drydock/ tests/
/home/bobef/miniconda3/bin/python3 -m pyright --pythonpath /home/bobef/miniconda3/bin/python3 drydock/
DRYDOCK_PY=/home/bobef/miniconda3/bin/python3 ./scripts/release.sh   # build + security scan
```

(Fresh clone elsewhere instead: `python3 -m venv .venv && . .venv/bin/activate
&& pip install -e ".[dev]"`, then `drydock` / `pytest tests/ -q` / `ruff` /
`pyright` are on PATH and need none of the workstation flags.)

## NON-NEGOTIABLE working rules (learned the hard way)

1. **Verify harness-side fixes in a REAL tmux TUI session** — not just pytest.
   `tmux new-session -d -s v3 -x 200 -y 50 'cd <dir> && PYTHONPATH=/data3/drydock-v3 /home/bobef/miniconda3/bin/python3 -m drydock --model gemma4 --provider vllm'`,
   then `tmux send-keys` a real prompt, `tmux capture-pane -p` to read it, and
   run the built code functionally. `--help` is NOT a test.
2. **Isolate `HOME` for TUI tests:** prepend `HOME=/tmp/v3_home` (mkdir it).
   This box also runs the operator's v2 install, and both share
   `~/.drydock/{config.toml,history}` — a dead-port test once leaked `base_url`
   into the operator's real config. Always sandbox HOME for test runs.
3. **Never let ruff/lint autofix delete side-effect imports.** `ruff --fix` once
   removed `import drydock.tools` from agent.py (it registers the tools), leaving
   an empty registry → the model emitted tool calls as TEXT and nothing ran.
   agent.py now calls `register_all()` explicitly with a regression test.
4. **NO custom eval harnesses.** Drive the TUI by hand, one prompt at a time.
5. **Advisory, never blocking** — loop/guard mechanisms inject context; only
   hard stops are the catastrophic denylist and max-tool-turns.
6. **Loop-breakers return a result string, never raise** (raising spawns its own
   loop on long tasks).
7. `scripts/security_scan.py` gates every release (exit 2 = HIGH = block).

## Credentials / env (workstation)
- GitHub: token at `~/.config/drydock/github_token` (user `fbobe321`). A global
  git credential helper now reads that file, so plain `git push` auto-auths and
  picks up any token rotation — no inline `GH_TOKEN` dance needed. (Set up
  2026-06-26.) `gh` at `/home/bobef/miniconda3/bin/gh`.
- Dev/test Python: `/home/bobef/miniconda3/bin/python3` (3.12, has textual/openai).
- tbench notifications paused via `/data3/drydock/.pause_tbench_*` flags.
- **PyPI: PUBLISHING IS LIVE (corrected 2026-06-26).** `drydock-cli` is on an
  ACTIVE account — token at `~/.config/drydock/pypi_token` (the OLD quarantined
  account's token is `~/.config/drydock/pypi_token.quarantined_account.bak`,
  unused). v3.0.46 was published this way. The earlier "reinstatement pending /
  publishing blocked" notes were WRONG. Publish: build, run the provenance scan,
  then `SETUPTOOLS_USE_DISTUTILS=stdlib python -m twine upload -u __token__ -p
  "$(cat ~/.config/drydock/pypi_token)" dist/*` (the env shim dodges a
  jaraco.functools circular import in this box's conda twine). `scripts/release.sh`
  documents the same.

## Suggested next steps (pick up here — 2026-06-26)
1. **Hands-on v3 TUI shakedown** — drive v3's real TUI in tmux (per rule #1),
   establish a clean baseline of what works / what's missing in the from-scratch
   build, with `capture-pane` evidence. This is how we find what to build next.
2. **Build `/compact` fresh in v3** (advertised in `/help`, not implemented —
   wire to `compaction.py`). Verify in tmux.
3. **Build the TUI-driven test path** (PRIMARY GOAL above): run tbench tasks
   through v3's real TUI instead of `-p`, then use it to find + fix real bugs.
4. Do NOT touch / read / port from the old `/data3/drydock` fork.
5. PyPI publishing WORKS now (see Credentials) — `drydock-cli` was at 3.0.32,
   v3.0.46 published 2026-06-26. Bump version → build → provenance scan → twine
   upload with the active token. (Docker republish still per operator.)

Earlier backlog (v3 hardening port, retrieval, etc.) is essentially done — see
the "What's implemented" + history sections above.
