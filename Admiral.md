# PRD: Admiral + Drydock (Adaptive Agent Orchestration System)

## 1. Overview
* **Execution Harness:** Drydock
* **Orchestration & Meta-Learning Layer:** Admiral
* **Vision:** **Drydock is a living harness that adapts to tasks and
  models, with Admiral as the adaptation engine.** Drydock handles
  deterministic execution of agentic tasks (tools, loops, file ops);
  Admiral observes the running harness, detects failure patterns,
  adjusts execution parameters (prompts, tool access, budgets), and —
  once mature — proposes gated source-level fixes that compound
  across sessions. A coding task and a research task shouldn't run
  under the same tolerances; Gemma 4 and Claude Opus shouldn't be
  prompted the same way. The harness adapts to both dimensions
  automatically, graded against the Directives rubric in §8a.

## 2. Core Problem
Current agent harnesses suffer from architectural fragility:
* **Static Execution:** They rely on hardcoded routing logic and brittle system prompts.
* **Zero Self-Awareness:** They cannot detect when they are stuck in a logical loop or hallucinating tool calls.
* **Lack of Domain Adaptation:** A coding agent and a research agent require different tolerances for loops and errors, but standard harnesses treat them identically.
* **The Root Issue:** We are deploying non-deterministic intelligence inside rigid, deterministic boxes. 

## 3. System Architecture
The system utilizes a dual-layer approach, strictly separating the "doing" from the "supervising."

```text
[ User Interface (TUI / CLI) ]
            |
            v
[ ADMIRAL (Meta-Controller) ] <--- Telemetry & Logs
            |                      |
      State Config                 |
            v                      |
[ DRYDOCK (Execution Engine) ] ----+
            |
    [ LLM Kernel ] <--> [ Tool / Env Runtime ]
```

## 4. Core Components

### 4.1 Admiral (The Orchestrator)
Admiral does not execute tasks. It ingests telemetry, evaluates execution health, and pushes state updates to Drydock.

* **A. Telemetry Ingestion:** Processes structured logs from Drydock (token usage, tool call frequency, error stack traces, execution latency).
* **B. Pattern & Anomaly Detection:**
    * *Loop Detection:* Identifies repeated sequential tool calls with identical outputs.
    * *Struggle Detection:* Identifies high token burn with zero file modifications or task progression.
* **C. State-Driven Modification (Not Code-Rewriting):**
    * Admiral modifies Drydock's behavior by updating its **State Config** (e.g., system prompt matrices, tool-access blocklists, max-loop constraints). It does *not* rewrite source code.
* **D. Learning Rate Scheduler:**
    * Controls how fast Admiral can alter Drydock's baseline configuration.
    * *Cold Start:* High tolerance for manual interventions, low threshold for prompt tweaks.
    * *Stable:* High threshold for baseline changes; relies on localized session memory.
* **E. Interventions & Versioning:**
    * Pauses Drydock when a failure threshold is crossed.
    * Prompts the user via TUI: *"Loop detected. Propose dropping [WebSearch] tool and appending directive: 'Use grep instead'. Approve? (Y/N)"*
    * Maintains a Git-like history of all config state changes for instant rollbacks.

### 4.2 Drydock (The Execution Harness)
Drydock is a highly modular, state-driven execution loop. 

* **A. State-Hydrated Execution:** Drydock boots up by reading the config injected by Admiral. If Admiral alters the system prompt or revokes a tool mid-flight, Drydock adopts the new state on the next loop iteration.
* **B. Modular Primitives:**
    * *Planner:* Maps the initial task.
    * *Executor:* Formats the prompt and parses the LLM output.
    * *Tool Router:* Safely executes the requested local commands.
* **C. Telemetry Emitter:** Emits a standardized JSON log for every step (e.g., `{"step": 4, "action": "tool_call", "tool": "bash", "status": "error", "latency": "1.2s"}`) directly to Admiral.

## 5. Example Execution Flow (Stuck State Mitigation)
1.  **Drydock:** Attempts to run a broken Python script 4 times. Each time, the LLM hallucinates the same incorrect fix.
2.  **Drydock:** Emits 4 identical error logs to the telemetry stream.
3.  **Admiral:** Detects the pattern. The confidence score for a "Logic Loop" hits 0.95.
4.  **Admiral:** Issues a `PAUSE` signal to Drydock. 
5.  **Admiral (via TUI):** *"Drydock is looping on a syntax error. Suggestion: Inject context directive 'Stop attempting to fix lines 40-50, rewrite the function from scratch.' Apply? (Y/n)"*
6.  **User:** Hits 'Y'.
7.  **Admiral:** Updates Drydock's temporary session config with the new directive. Issues `RESUME`.
8.  **Drydock:** Re-evaluates the prompt with the new directive and breaks the loop.

## 6. Project Risks & Mitigations
* **Risk:** The system becomes too complex to debug (who broke it, Admiral or the LLM?).
    * *Mitigation:* Strict, readable audit logs. Every change Admiral makes to Drydock's state must be written to a plain-text `admiral_history.log`.
* **Risk:** Overfitting to specific user quirks.
    * *Mitigation:* Separate the memory stores. Maintain a `Session State` (wiped every run) and a `Global State` (persistent). Admiral must prove a fix works across multiple sessions before committing it to the Global State.
* **Risk:** "Hallucinated" fixes by Admiral.
    * *Mitigation:* Admiral uses hardcoded heuristic triggers for Phase 1 MVP, *not* an LLM, to decide when to intervene. 

## 7. MVP Scope (Phase 1)
* **Drydock:** Basic CLI/TUI that can execute shell commands, read/write files, and query an LLM. Must emit structured JSON logs.
* **Admiral:** A background Python thread that tails Drydock's logs.
* **Features:**
    * Regex-based loop detection.
    * Hard pause capability.
    * Ability to let the user manually inject a new system prompt directive mid-run without restarting the context window.

***

## 8a. Admiral Directives — the rubric Admiral holds Drydock to

These are the principles every detector, every LLM meta-analysis
prompt, and every proposer/validator decision must trace back to.
If a new detector can't be justified by one of these directives, it
doesn't belong in Admiral. Conversely: any drydock behavior that
violates one of these is a legitimate target for intervention.

### A. Correctness before velocity

1. **Verify, don't assume.** Never mark a task done without running
   the thing you claim works — tests for code, command execution
   for CLIs, a real render check for UI. `--help` is not a test.
2. **No fabrication.** Tool results, filenames, function names,
   library APIs must exist. If the model doesn't know, it says so.
   Don't invent stubs that pass `--help` but fail every real call.
3. **Test-gated progress.** Writing a file ≠ feature done. Feature
   done = test covering the feature passes. New feature without a
   new test is a half-finish.
4. **Don't silently swallow errors.** If a tool fails, the failure
   reaches the user. A retry loop that hides a 500 for 10 minutes
   is worse than stopping.

### B. Real progress per turn

5. **Every tool call must reduce uncertainty or change state.**
   Read-read-read-read without writing is thrashing; detect it and
   force either a write or a "stop and ask the user".
6. **No loops — pivot.** Identical tool call with identical arguments
   ≥3 times is a loop; the harness must nudge a different approach,
   not retry the same call.
7. **Respect scope.** One user ask = one user answer. Don't invent
   follow-up work (extra tests, refactors, tangential files) the
   user didn't request — that's where runaway sessions come from.
8. **Commit to a plan.** After enough exploration (≥20 reads/greps
   without a write), either write code or tell the user what's
   blocking. Endless browsing is not progress.

### C. Safety and reversibility

9. **Destructive ops need a human.** `rm -rf`, `git reset --hard`,
   `git push --force`, `DROP TABLE`, hook bypass (`--no-verify`) —
   the harness stops and asks. Non-destructive reads/writes don't.
10. **Never bypass verification.** No `--no-verify`, no force-skipping
    CI, no disabling tests to "just ship it." If a hook fails, fix
    the cause, don't silence the signal.
11. **Secrets stay secret.** No token/API-key content in logs,
    commits, or assistant messages. Redact anything matching a
    known secret pattern before surfacing it.

### D. Transparency

12. **Every action is auditable.** Tool calls, errors, and harness
    interventions (including Admiral's own) produce a trace a human
    can read after the session. No silent state changes.
13. **Output is readable.** Assistant text to the user has paragraph
    breaks, list markers where appropriate, and doesn't bury the
    answer in filler. Walls-of-text are a UX bug.
14. **Honest status.** "Task Completed" means the task is actually
    completed, not "I made an attempt." False-success claims are
    the single worst failure mode — harder to detect than bugs.

### E. Rigor in changes

15. **Small, composable edits.** One logical change per tool call;
    don't touch unrelated files during a bug fix. Cleanups belong
    in their own commits.
16. **Reuse existing patterns.** If the codebase already has a
    utility/pattern, use it. Don't parallel-invent.
17. **Minimal, targeted fixes.** A bug fix doesn't need surrounding
    refactors; a one-shot script doesn't need an abstraction layer.
    Over-engineering is a correctness risk because it enlarges the
    diff that has to be reviewed.

### F. Context hygiene

18. **Truncate noise, keep load-bearing context.** Stale `read_file`
    outputs, verbose `ls` dumps, and chatty tool results get
    summarized or discarded before they fill the window.
19. **Don't re-read unchanged files.** Cache mtime + content hash;
    a second read of an unchanged file is a sign of lost context.

### G. Meta-discipline (applies to Admiral itself)

20. **Measure twice, inject once.** Before applying an intervention,
    confirm the finding is not a false positive — dedup window,
    minimum-confidence threshold, cross-check against recent events.
21. **Never escalate what a prompt can fix.** Phase 2's Opus
    escalation is expensive and slow; reserve it for cases where
    local analysis explicitly says STUMPED.
22. **Log every decision, even no-ops.** Admiral's own actions
    (including choosing NOT to intervene) must be auditable, or
    users can't tell what it's doing.

**How to use this section in prompts.** The Phase 2 local-LLM
analyzer and Phase 3 proposer prompts must cite the specific
directive number(s) a finding violates. Example: `"The agent has
called read_file 22 times with no writes. Directive B5 (uncertainty
reduction) and B8 (commit to a plan) are both being violated.
Propose a directive that forces one of: (a) write code now, (b)
state what's blocking."`

***

## 8. Implementation Status (2026-04-17)

### Phase 1 — SHIPPED (module present in v2.6.137, wiring in flight for v2.6.138)

Architecture: **in-process** (option C). Admiral runs as an asyncio.Task
inside the Textual app's event loop — same process as the agent, no IPC.
Auto-apply default (no Y/N approval) per user directive.

Files:
* `drydock/admiral/__init__.py` — public API (`attach(agent_loop)`).
* `drydock/admiral/detectors.py` — `detect_tool_call_loop`,
  `detect_struggle`, `run_all`. Pure functions; return `Finding(code, directive)`.
* `drydock/admiral/interventions.py` — `apply(agent_loop, finding)` →
  calls `AgentLoop._inject_system_note` + appends to audit log.
* `drydock/admiral/history.py` — append-only timestamped log at
  `~/.drydock/logs/admiral_history.log`.
* `drydock/admiral/worker.py` — `AdmiralWorker` async task that polls
  every 5s, dedup window 60s per finding code.
* `tests/test_admiral.py` — 5 tests for detector correctness.

Integration:
* `drydock/cli/textual_ui/app.py::on_mount` — `admiral.attach(agent_loop)`
  after approval callbacks are set. Stored on `self._admiral`. Any Admiral
  import/startup error is logged but never prevents the TUI from starting.

Detectors (Phase 1):
1. **Loop detection** — 3+ identical (tool_name + arguments) calls in a
   row → inject directive telling the model to stop the loop.
2. **Struggle detection** — 20+ non-write tool calls since the last
   `write_file`/`search_replace`/`edit_file` → inject "write code or
   tell the user you're stuck."

### Phase 2 — SHIPPED (v2.6.139)

* `drydock/admiral/llm_analyzer.py` — `analyze(agent_loop, finding)`
  sends the last 12 turns + the finding code + the **Directives rubric
  (§8a)** to the local Gemma 4 backend (reuses `AgentLoop.backend`,
  temp 0.1, 400 max tokens). Expected response format:
  `DIRECTIVE [Bx,Cy]: <text>` (cites violated directive codes) or
  `STUMPED: <reason>`.
* `drydock/admiral/opus_escalator.py` — `escalate(finding, messages)`
  tries Anthropic SDK (if `ANTHROPIC_API_KEY` set) then falls back to
  `claude -p "<prompt>"` subprocess. Rubric included in the Opus
  prompt too; response starts with `[Bx,Cy]` bracket. Capped at 3
  calls per session, 90s timeout per call. Model: `claude-opus-4-7`.
* `drydock/admiral/worker.py::_resolve_directive` — escalation ladder:
  1. local LLM (reuses agent's backend), 2. Opus if stumped,
  3. canned fallback directive. Every source logged to
  `admiral_history.log` as a `directive-source` entry; the probe +
  dashboard surface these counts in real time.

### Phase 2 — ORIGINAL DESIGN (kept for reference)

Escalation ladder for when heuristics aren't enough:

```
[Admiral detector fires]
        |
        v
[local Gemma 4 meta-analysis] — send the last N turns + the finding to
                                the local vLLM endpoint asking "what
                                is really going wrong? what directive
                                would unstick the agent?"
        |
        v (low confidence / model also stumped)
[Claude Code (Opus) escalation] — shell out to `claude -p "<analysis>"`
                                   (or the Anthropic SDK) for a second
                                   opinion. Cap at N escalations per
                                   session.
        |
        v
[Admiral applies Opus's directive via _inject_system_note]
```

Open design questions for Phase 2:
* Local LLM reuse: should Admiral reuse the agent's own backend
  connection, or open its own at a different temperature?
  → **Lean:** reuse `AgentLoop.backend` with a separate message list
  and lower temperature (0.1 vs 0.7) so the analysis is deterministic.
* Opus access: `claude -p` CLI or `anthropic` SDK?
  → **Lean:** SDK when `ANTHROPIC_API_KEY` is set; fall back to
  `claude -p` when not (so users who already have Claude Code auth
  don't need to re-enter a key).
* Rate-limiting: prevent Opus blast during a bad run. Max 3 escalations
  per session by default. Override via env var.

### Phase 3a — SHIPPED (v2.6.141): Hyperparameter adaptation per (model, task)

**Goal.** Admiral learns the strengths and weaknesses of the current
model + tool set and tunes the harness hyperparameters accordingly,
without touching source. Think: "grow the harness around the model."

**What's a hyperparameter here?** Anything that's currently a constant
in drydock but should vary by (model, task_type):

| Knob                           | Source today                        | Admiral bound      |
|--------------------------------|-------------------------------------|--------------------|
| `PER_PROMPT_BUDGET_SEC`        | `agent_loop.py` (30 min default)    | 5 min .. 60 min    |
| Hard stop tool-call count      | `agent_loop.py` (100 default)       | 30 .. 250          |
| Streaming on/off               | model config                        | on/off per model   |
| Thinking level (high/low/off)  | `adaptive_thinking` logic           | off/low/high       |
| Enabled tools                  | `gemma4.md` already disables 7      | ≥ minimum safe set |
| System prompt file             | `cli.md` vs `gemma4.md`             | any `prompts/*.md` |
| Temperature                    | backend call sites                  | 0.0 .. 0.9         |
| Loop detector window (≥3)      | `detectors.py`                      | 2 .. 6             |
| Struggle detector threshold    | `detectors.py` (20)                 | 10 .. 40           |
| Wrap-up warning thresholds     | `agent_loop.py` (30/60)             | bounded by hard    |

**Task-type inference (best-effort).** Admiral classifies the current
session into one of: `build` (creating a new project from a PRD),
`bugfix` (modifying existing code to fix a failing test), `explore`
(answering questions / reading code), `refactor` (restructuring
without behavior change), `unknown` (default). Signals used:
* File extensions touched (`.py` alone → code; `.md` dominant → docs).
* Ratio of reads to writes (high reads, few writes → explore).
* Tool mix (many `search_replace` → bugfix; many `write_file` → build).
* User prompt keywords (first-turn regex match: "build", "fix",
  "explain", "refactor").

**Model identification.** Reads `AgentLoop.config.get_active_model()`.
Keyed by `(model_name, task_type)` so Gemma 4 in build mode gets
different hyperparameters than Gemma 4 in explore mode.

**Metrics collected per session end.**
* `outcome`: success (user said thanks / closed happy) | failure
  (user said STOP / session ended with error).
* `tool_calls_per_feature`: total tool calls ÷ distinct files written.
* `time_to_first_write`: wall-clock from user prompt to first
  `write_file` or `search_replace`.
* `loop_fires`, `struggle_fires`: Admiral finding counts.
* `opus_escalations`: how often local LLM was stumped.
* `user_interrupts`: STOP-typed or Ctrl+C count.
* `per_prompt_budget_hits`: how often the 30-min cap was hit.

Stored in `~/.drydock/admiral_metrics.jsonl` (one line per session).

**Adaptation policy (explicit, bounded, reversible).** Simple rules
for MVP — no ML — because complexity here is a footgun:

1. After ≥ 5 sessions for a `(model, task)` tuple, compute the
   success-rate vs baseline.
2. If success rate is ≥ 0.2 below baseline, Admiral tunes ONE knob
   (the one most correlated with failure) by ONE step toward the
   bounded range, for that `(model, task)` tuple only.
3. Next 3 sessions re-measure. If success rate doesn't recover in 3
   sessions, revert the knob.
4. Only ONE knob at a time per tuple. Never two simultaneous tweaks.

**Storage.** `~/.drydock/admiral_tuning.json`:
```json
{
  "gemma4+build": {
    "PER_PROMPT_BUDGET_SEC": 2400,
    "struggle_threshold": 25,
    "_rationale": "Exploration phase needs longer budget; build tasks write many files."
  },
  "gemma4+bugfix": {
    "struggle_threshold": 12,
    "_rationale": "Bugfixes should write quickly after grep — tighter struggle."
  }
}
```

**Loading.** At AgentLoop init, Admiral injects the `(model, task)`
tuning by setting attributes BEFORE the loop starts. Task type for
session-start is `unknown` until the first user prompt classifies it,
then Admiral re-evaluates once and possibly re-tunes.

**Hard invariants (complement Phase 3 invariants):**
1. Knob changes ALWAYS within bounded ranges (table above).
2. Admiral NEVER mutates source files to change hyperparameters —
   only the JSON state file.
3. User can nuke the tuning file any time to revert to defaults.
4. If the tuning JSON is malformed, Admiral ignores it and logs; it
   never crashes the harness.

**Modules (to be created):**
* `drydock/admiral/task_classifier.py` — `classify(messages) -> str`.
* `drydock/admiral/metrics.py` — session-end metric collector +
  JSONL writer.
* `drydock/admiral/tuning.py` — load/save `admiral_tuning.json`,
  apply-on-init hook, bounds enforcement.
* `drydock/admiral/policy.py` — the 4-rule adaptation policy above.
* AgentLoop integration: one-line hook in `__init__` that applies the
  tuning for `(model, unknown)` initially, and a session-end hook
  that logs a metrics line.

---

### Phase 3b — SHIPPED (v2.6.141, default-OFF via DRYDOCK_ADMIRAL_PROPOSER=1)

**Goal.** Close the loop from detection → proposed fix → validated
branch, without ever writing to `main` or PyPI automatically. The
supervisor can diagnose, draft a patch, run the test suite against
it, and stage a branch — but a human still presses go.

**Hard invariants (must never be violated):**
1. Admiral NEVER commits to `main`.
2. Admiral NEVER pushes to GitHub.
3. Admiral NEVER invokes `scripts/publish_to_pypi.sh`.
4. Admiral NEVER bypasses pre-commit hooks or CI.
5. Admiral NEVER runs destructive git ops (reset --hard, clean, force-push).
6. A proposed change is staged only if **the full test suite passes**
   in a clean git worktree against the patched code.

**Proposer prompt template (cites directives):**

```
You are Admiral's code proposer. A recurring finding has survived
prompt-only interventions. Propose a minimal unified diff that fixes
the root cause, and cite which directive codes from the Admiral
rubric (A1..G22) the current code violates.

{rubric}

Recurring finding: {code} — seen in {n_sessions} sessions.
Previous prompt-only interventions: {intervention_history}
Repro context (last N turns): {context}

Respond in this format:

DIRECTIVES VIOLATED: [A3, B8, ...]   # codes from rubric
RATIONALE: <one paragraph, why this diff is the right fix, grounded
            in the cited directives>
DIFF:
```diff
<unified diff, narrow scope, touches only the files needed>
```
```

**Pipeline (per finding that would benefit from a code change):**

```
[Finding fires — and has >= N repeat occurrences across sessions]
             |
             v
[proposer.py]     ← local LLM, then Opus escalation (reuse Phase 2
                    escalator). Produces a unified diff + rationale.
             |
             v
[validator.py]    ← git worktree add → apply patch → pytest → ruff →
                    pyright (best-effort). Hard fails if any red.
             |
             v
[stager.py]       ← creates local branch `admiral/<ts>-<code>`,
                    commits patch with "admiral-proposed:" prefix,
                    NEVER pushes. Drops a summary into
                    ~/.drydock/admiral_proposals/<ts>.md (diff + rationale +
                    test-run output + rollback command).
             |
             v
[TUI notification] ← Banner/toast: "Admiral staged a proposed fix on
                     branch admiral/<ts>. Review with
                     `git diff main..admiral/<ts>` then merge manually
                     or run `/admiral-apply admiral/<ts>`."
```

**`/admiral-apply <branch>` slash command (new)**
* Merges `admiral/<ts>` into `main` with `--no-ff` (preserves history).
* Tests run AGAIN on the merged tree as belt-and-suspenders.
* Still does not publish — the existing `publish_to_pypi.sh` /
  `auto_release` cron is the only path to PyPI, and that's unchanged.
* On failure, `git merge --abort` and keep the proposal branch around.

**`/admiral-reject <branch>` slash command (new)**
* Deletes the local branch, archives the proposal markdown to
  `~/.drydock/admiral_proposals/rejected/`, records user-visible feedback
  ("why rejected") that proposer.py reads next time so we don't
  re-propose the same fix.

**Promotion criteria (the "compounding-improvement" part):**
Admiral doesn't propose a code change the first time a pattern fires.
Only after:
* Same detector code fires in ≥ **3 different sessions**, AND
* Prompt-only interventions failed to unstick in at least one of them
  (measured by: same finding code re-fired within 10 turns after a
  Phase 1/2 intervention).

This biases Admiral toward code changes only for recurring structural
issues, not one-off weirdness. Counters tracked in
`~/.drydock/admiral_state.json` (persisted across sessions).

**Module layout (to be created):**
* `drydock/admiral/proposer.py` — drafts a unified diff.
* `drydock/admiral/validator.py` — worktree + pytest runner (wraps
  `git worktree add` + `pytest` subprocess; strict failure semantics).
* `drydock/admiral/stager.py` — creates branch, commits, writes the
  proposal markdown. No push, ever.
* `drydock/admiral/persistence.py` — cross-session counters at
  `~/.drydock/admiral_state.json` (promotion-criteria bookkeeping).
* `drydock/cli/commands.py` — add `/admiral-apply` and
  `/admiral-reject` slash handlers.

**Risks & mitigations:**
* *Risk:* Admiral proposes a patch that passes tests but regresses
  real user workflows (test gap).
  *Mitigation:* merge is still manual; real user session is the
  actual gate. Plus the `auto_release` cron's stress suite catches
  semantic regressions before PyPI.
* *Risk:* Storage of rejected proposals leaks sensitive context
  (env vars, API keys from session logs baked into the diff rationale).
  *Mitigation:* the proposer should never include `env`/`config.toml`/
  token files in its diff scope, and the rationale redactor strips
  anything matching known secret patterns before writing.
* *Risk:* Admiral keeps proposing the same already-rejected patch.
  *Mitigation:* `/admiral-reject` records a fingerprint of the patch;
  proposer skips patches with matching fingerprints.

### Observability — SHIPPED (2026-04-17)

**Goal.** Give a human a live view of what Admiral is doing without
having to tail a log file.

**Components:**
* `scripts/admiral_probe.py` — stdlib `ThreadingHTTPServer` on the
  drydock host (192.168.50.22:8878). Read-only. Endpoint
  `GET /api/admiral` returns a snapshot JSON:
  ```
  { ts, drydock_version, running_drydock_pids,
    history_tail [last 50 admiral_history.log entries],
    event_counts,
    directive_source_counts {local-llm|opus|canned},
    tuning { (model, task): {knob: value, _rationale: ...} },
    recent_metrics [last 10 session metrics] }
  ```
  Also serves `/healthz`. CORS `*` so the dashboard can fetch directly
  if needed. Launch: `nohup python3 scripts/admiral_probe.py --bind
  0.0.0.0 --port 8878 &`. Bound to LAN only via UFW rule.

* **Dashboard tab** on the RSI dashboard box (192.168.50.21:8877).
  Source: `/data3/RSI/dashboard/{app.py,templates/index.html}`.
  Proxy route `/api/admiral` in `app.py` fetches the probe. Tab UI
  has 4 summary cards (probe status, event counts, directive sources,
  active tuning) + a recent-interventions table + session metrics.
  Auto-refreshes every 5s while active. Env override:
  `ADMIRAL_PROBE_URL=http://<host>:<port>/api/admiral`.

* **Infra.** SSH pubkey auth installed `192.168.50.22 → .21` so
  dashboard edits don't need passwords. UFW rule on .22:
  `ufw allow from 192.168.50.0/24 to any port 8878 proto tcp`.

**Still TODO for observability:**
* systemd unit for the probe so it survives reboot (currently nohup
  under user shell; PID noted in continuity notes).
* Per-session drill-down view (click a finding code, see the
  conversation turns that triggered it).

### Phase 4 — IDEAS (not started)

* **Learning-rate scheduler (PRD §4.1.D)** — cold start accepts many
  tweaks, stable mode accepts few. Implement by tracking how many
  interventions the user manually undid via slash-commands.
* **Session vs Global memory (PRD §6)** — session state wiped per run;
  global state only promoted if a fix worked across ≥2 sessions.
* **TUI widget** — a collapsible Admiral panel showing recent findings
  and their outcomes, so the user can audit the supervisor.

## 9. Continuity Notes (if this session is lost)

**Latest shipped:** v2.6.141 — Phase 3a (hyperparameter adaptation)
and Phase 3b (gated code proposals, default-OFF) both in PyPI.

**Shipped this session (2026-04-17):**
* v2.6.134 — wall-of-text inline-marker breaker (issue #8).
* v2.6.135 — know-when-to-stop prompt (TODO vs SIMPLE mode).
* v2.6.136 — per-prompt budgets 15→30 min, 50→100 tool calls
  (issue #9).
* v2.6.137 — aggressive sentence-boundary splitting for flat prose
  (issue #8 reopen).
* v2.6.138 — Admiral Phase 1 module + TUI integration.
* v2.6.139 — Admiral Phase 2 (local-LLM analyzer, Opus escalator,
  Directives rubric in prompts).
* v2.6.140 — full `.vibe → .drydock` rename (zero vibe refs left).
* v2.6.141 — Phase 3a (task_classifier, metrics, tuning, policy +
  AgentLoop integration) + Phase 3b (proposer, validator, stager +
  /admiral-apply, /admiral-reject, /admiral-status slash commands)
  + scripts/stress_watcher.py harness-level babysitter.

**Files (check `git log --oneline -30`):**
* `drydock/admiral/{__init__,detectors,interventions,history,worker,
  llm_analyzer,opus_escalator}.py`
* `tests/test_admiral.py` — 5 tests passing.
* `drydock/cli/textual_ui/app.py::on_mount` — `admiral.attach()`.
* `drydock/cli/textual_ui/widgets/messages.py` — wall-of-text fixes.
* `drydock/core/agent_loop.py` — bumped per-prompt budgets.
* `drydock/core/prompts/gemma4.md` — stop-logic + formatting rules.
* `scripts/admiral_probe.py` — read-only HTTP probe (this host only).
* `Admiral.md` — this PRD.

**Audit log:** `~/.drydock/logs/admiral_history.log` — every intervention
applied, including the `directive-source` (local-llm / opus / canned).

**User directives recorded this session:**
* Architecture: option C (in-process Textual worker).
* Approval: auto-apply, no Y/N dialog.
* Phase 2: local LLM first; escalate to Claude Code (Opus) when
  stumped. SHIPPED v2.6.139.
* Phase 3: code proposals — YES, but gated. Never auto-commit to
  main. `/admiral-apply <branch>` merges, user decides.
* Phase 3a: Admiral adapts hyperparameters per (model, task_type) —
  "harness grows around the model."
* Directives rubric must be the grading source for every detector
  and prompt.
* Dashboard: add an Admiral tab to http://192.168.50.21:8877/
  (DONE).
* SSH pubkey installed .22 → .21 (user = `bobef`; password was
  `lis4351` — needed only for initial key push, no longer required).
* Keep updating this PRD so a session loss doesn't cost context.

**Live infrastructure (running right now):**
* `admiral_probe.py` on `192.168.50.22:8878` — `nohup &` under bobef
  shell, PID noted in `/tmp/admiral_probe.log`. NOT systemd yet.
* Dashboard tab live at http://192.168.50.21:8877/ (click Admiral).
* Stress test `scripts/stress_shakedown.py` — PID 1860693, run
  started ~Apr-16, currently at ~826/1658 (approaching 50%).
  DO NOT interrupt.

**Open follow-ups (post-observation period):**
* systemd the admiral_probe (currently `nohup &`, dies on logout).
* TUI widget showing recent Admiral findings (in-app audit view).
* More detectors driven by observed data: hallucinated tool names,
  edit-undo-edit ping-pong, PRD/spec drift.
* Learning-rate scheduler (Phase 4).
* Session vs Global memory split (PRD §6).
* Tune Phase 3a knob bounds + Phase 3b promotion criteria from real
  finding-rate data after the 24h observation window.

## 10. Observation mode (active 2026-04-17 → 2026-04-18)

Per user directive ("let's slow down, and watch for now"). All
Admiral feature work is paused. What's still running automatically:

* **Stress-test status check** every 30 min (Telegram).
* **GitHub issue check** every hour for fbobe321 issues.
* **stress_watcher.py** on the live stress run (PID 1860693) —
  detects stalls/timeout-spikes/completion, emits Telegram + audit log.
* **admiral_probe.py** on `192.168.50.22:8878` — dashboard tab
  consuming it.
* **Admiral itself** is live in every drydock TUI session — Phase 1
  + Phase 2 fully active, Phase 3a tuning enabled (no-op until
  `admiral_tuning.json` gets written), Phase 3a policy gated behind
  `DRYDOCK_ADMIRAL_POLICY=1`, Phase 3b proposer gated behind
  `DRYDOCK_ADMIRAL_PROPOSER=1` (both default OFF).

**Cron `7a2a593c` fires Sat 2026-04-18 18:13 CDT** — at that point I
will read `~/.drydock/logs/admiral_history.log` event counts, scan
`~/.drydock/logs/admiral_metrics.jsonl`, check the dashboard, and
propose 1–2 next features grounded in what actually fired in the
past 24h. **No new code without observed evidence it's needed.**

***

## 11. Deep Noir integration pilot (2026-04-23, IN FLIGHT)

Goal stated by user: "I want Deep Noir framework to work with Admiral
and meta-harness to improve drydock and Gemma 4." Not a paper — a
shippable tool.

### 11.1 Intended architecture

DN plugs in as a **new intervention class** for Admiral, alongside the
existing prompt/tool-config interventions:

  Admiral detector → classifies failure mode →
      (a) prompt-level fix (current)
      (b) activation-space steering hook (new, via DN)

  Meta-harness (`research/experimenter.py`) already mutates scalar knobs
  (e.g., `wrap_up_warn_at`, `REPEAT_FORCE_STOP_THRESHOLD`); DN extends
  the search space to `(layer, head_subset, magnitude)` tuples per
  failure mode. The experimenter scores configs the same way — by
  medium-hard PRD success rate.

The asset DN needs is a **labeled probe set**: real decision-point
contexts from drydock sessions, each tagged with the "correct"
next-token class. Discovery then finds the (L, K, M) that maximizes
labeled accuracy.

### 11.2 What was built this session

* **Probe extractor** (`/data3/Deep_Noir_1/drydock_probes/build_probes.py`
  + focused variants): mines `~/.drydock/logs/session/**/messages.jsonl`
  for every assistant decision point, renders the chat-history context,
  and labels by whether the turn emitted a tool_call or text. Output:
  - `drydock_act_ask.jsonl` — 5979 probes, 296 sessions
  - `drydock_wrap_vs_act.jsonl` — 360 balanced probes (act vs text)
  - `drydock_1b_compat.jsonl` — 240 probes portable to non-Gemma-4
    tokenizers

* **Discovery driver** (`discover_steering.py`) — model-agnostic wrapper
  around DN's `SteeringDiscoveryEngine`. Loads any HF Gemma, runs the
  5-phase pipeline, emits a `SteeringConfig` JSON.

* **Sanity check on Gemma-3-1B-IT**: Phase-1 layer ranking reproduced
  the paper's "Gemma uses deep layers" finding on drydock-domain data.
  Top layers 18–24 match the sentiment results in Figure 1. Pipeline
  works end-to-end.

* **vLLM logprob eval harness** (`evaluate_wrapup_detector.py`) — was
  built to ship a logprob-based pre-generation detector without
  activation hooks. Abandoned; see §11.4.

* **Dependencies** added to `miniconda3` base env (harmless):
  `transformers @ git+main`, `compressed-tensors`, `peft`,
  `bitsandbytes`, `accelerate`.

* **Pause sentinel added to `scripts/vllm_failover.sh`**
  (`/data3/drydock/.pause_vllm_failover`) — the cron was auto-respawning
  the container and kept clobbering the GPU. Sentinel is user-visible
  and reversible; remove the file to resume normal failover.

### 11.3 Three blockers hit (honest)

1. **`Gemma-4-26B-A4B-it-AWQ-4bit` checkpoint cannot be loaded in
   transformers.** The directory is mis-named — the format is
   `compressed-tensors` with MoE `_packed`/`_scale` expert weights.
   Transformers (5.6.2 AND git-main 5.7.0.dev0) does not decompose
   those into `gate_up_proj` / `down_proj` at load time. Result:
   `lm_head.weight` and all MoE expert projections are meta-initialized
   (random). vLLM loads this fine because it has its own
   compressed-tensors path; HF transformers does not. This is an
   upstream gap, not something we can patch quickly.

2. **Driver/torch mismatch in the drydock env.** `miniforge3/envs/drydock`
   has `torch 2.11.0+cu130`; driver is 575 (CUDA 12.9). Incompatible.
   `miniconda3` base has `torch 2.7.1+cu126` which works. All DN work
   now uses `/home/bobef/miniconda3/bin/python3`, not the drydock env.

3. **The wrap-up-hijack detector I tried to ship is based on
   mis-labeled data.** "Previous turn ended; awaiting your next
   instruction." — the phrase I treated as a Gemma 4 pathology for a
   day — is actually drydock's OWN filler injected at
   `drydock/core/agent_loop.py:3006` via `_ensure_assistant_after_tools`.
   Every match in every session has `prev_role: tool` and
   `next_role: user`. An Admiral detector that strips this would be
   stripping drydock's own checkpoint marker. Killed that path.
   The 5344 tool-call probes (label=1) are still real Gemma 4 behavior;
   the 69 "I have …" / "The …" text probes are real completion
   summaries; only the 182 "Previous turn ended" probes were mis-
   attributed.

### 11.4 Decision point — user's call

Unquantized Gemma 4 bf16 **download complete** at
`/data3/Deep_Noir_1/models/gemma-4-26b-a4b-it/` (51.6 GB, both
safetensors shards present, cache cleaned). Option 1 is immediately
actionable. Three forward paths:

* **Option 1 — Wait for bf16, run DN on it.** transformers loads
  vanilla bf16 without the compressed-tensors MoE gap. 26 B bf16 is
  too big for a single 16 GB GPU, so load with `bitsandbytes` 4-bit +
  `device_map="auto"` across both 4060 Ti's. Hooks then need per-
  device direction/mask tensors — 1 small DN patch, not days of work.
  Probe contrast `<|tool_call>` vs `" Previous"` is still useful:
  steers toward "emit tool_call" vs "end turn silently so drydock has
  to fill." Highest-value path that actually gets us a tool for the
  deployment model. ~30–60 min after download finishes.

* **Option 2 — Stop and catalog.** Probes + drivers + GPU plumbing are
  reusable. Park the pilot until transformers grows MoE+compressed-
  tensors support for the existing AWQ-4bit checkpoint. Zero further
  GPU burn; clean resume later.

* **Option 3 — Re-label probes, try a different contrast.** Manually
  split the 453 non-filler text responses into "legitimate task-
  complete" vs "premature-stop". Needs 1–2 hr of labeling before any
  model work. Only justifies itself if Option 1 fails.

**Recommendation**: 1, with 2 as the fallback if bf16 + bnb 4-bit + DN
still hits some third bug we can't solve today.

### 11.5 Staged infrastructure (what survives a session loss)

* Pause sentinels active: `/data3/drydock/research/STOP`,
  `/data3/drydock/.pause_auto_release`,
  `/data3/drydock/.pause_vllm_failover`. **Remove all three** to
  restore normal automation.
* vLLM was restarted with `--gpu-memory-utilization 0.90 --max-model-len 32768`
  (down from 0.95 / 131072) because TP1 was OOMing during CUDA graph
  capture. Container name unchanged (`gemma4`); to restore the
  original config, `docker rm gemma4 && /data3/Models/start_gemma4.sh`.
* Research experimenter is stopped (STOP sentinel). Best-metric
  snapshot at pause: 4.725. Post-ban-list trend was negative (random
  fallback was underperforming the banned `wrap_up_warn_at=100` LLM
  proposal, which suggests the LLM proposer was earning its keep even
  when it looked narrow).
* DN source-of-truth: `/data3/Deep_Noir_1/` (separate repo, MIT). All
  pilot code under `drydock_probes/` subdirectory; no changes to the
  published paper or experiments.

### 11.6 When resuming

1. Verify bf16 download completed: `du -sh /data3/Deep_Noir_1/models/gemma-4-26b-a4b-it`
   should be ~49 GB, with both `model-00001-of-00002.safetensors` and
   `model-00002-of-00002.safetensors` present and `.cache/` empty.
2. Test bf16 load in transformers (single process, 2-GPU device_map,
   bnb 4-bit). Confirm LOAD REPORT shows **no MISSING keys**.
3. If clean: `python3 discover_steering.py --model_path …/gemma-4-26b-a4b-it
   --probes probes/drydock_wrap_vs_act.jsonl --meta probes/…meta.json
   --probe_limit 100 --max_layers 5 --max_K 4 --model_kwargs '{"load_in_4bit":true,"device_map":"auto"}'`.
4. If accuracy gain > 0 on labeled probes, that's the direction to
   wire into Admiral as a new intervention. The hook itself needs a
   separate serving path since vLLM doesn't expose activation hooks —
   likely a sidecar HF-transformers process that Admiral queries only
   when a detector fires. Design TBD after we see the gain number.

### 11.7 Overnight (2026-04-24) autonomous session — results

User directive: "try them all, if needed, keep going, heading to bed
now." Executed Option 1 first, then pivoted to a data-driven version of
Option 3 when Option 1 hit hard blockers.

**Option 1 (DN on Gemma 4 26B) — BLOCKED by loader plumbing.**
Six load-attempt variants all failed on different axes:
  - AWQ-4bit (compressed-tensors MoE): `lm_head.weight` + expert
    projections meta-initialized. Upstream transformers gap, confirmed
    on both 5.6.2 and 5.7.0.dev0.
  - bf16 + fp16 dtype + device_map=auto: last layer on `meta`, forward
    crashed. 26.5B × 2 B = 53 GB doesn't fit 2×16 GB.
  - bf16 + bnb-4bit + transformers-dev: `Params4bit.__new__()`
    TypeError on `_is_hf_initialized` kwarg. bnb 0.49.2 missing it.
  - bf16 + bnb-4bit + transformers 5.6.2 stable: same TypeError
    (transformers 5.6.2 also passes the kwarg, not just dev).
  - Same with monkey-patched Params4bit to swallow the kwarg:
    `Tensor.item() cannot be called on meta tensors` in bnb's
    state_dict save-path when CPU offload is enabled.
  - Same with explicit device_map (17 layers GPU0, embeds+13 layers
    GPU1) and CPU offload disabled: CUDA OOM on GPU 0 at 15 GiB during
    load-time fp16 intermediate (bnb reads fp16 shard, quantizes on
    GPU, moves on).

Bottom line: transformers + bnb + Gemma 4 26B MoE is not a working
combination today on 2×16 GB hardware. Would need either a newer bnb
release that accepts `_is_hf_initialized` (trivial) AND a loader that
streams-and-quantizes without a full fp16 intermediate per shard
(non-trivial), or an alternate serving path (vLLM already loads it
cleanly, but doesn't expose activation hooks; forking vLLM to add hooks
is the real DN-on-Gemma-4 path and is a ~week-long project).

**Pivot — session log behavioral mining (this IS the tool, no DN needed).**
Ran `mine_behavior_patterns.py` across 400 real drydock sessions (14,822
messages) to count actual model pathologies directly from traces:

```
IDENTICAL_TOOL_REPEAT        481 fires  (1.20 per session)
EMPTY_ASSISTANT_AFTER_TOOL   254 fires  (0.64 per session; 61% of sessions hit)
TOOL_ARGS_IGNORE_RESULT       45 fires  (0.11 per session)
```

The existing `detect_tool_call_loop(window=3)` already catches
IDENTICAL_TOOL_REPEAT but fires advisory-only; the model ignores the
nudge (CLAUDE.md learning #2: "Gemma 4 ignores advisory nudges"). The
other two patterns have no current detector.

**Two new proposed detectors, code written + validated end-to-end:**

  `drydock/admiral/detectors_proposed.py` (NEW, NOT wired into
  `run_all` — observation mode from §10 still binding).

  1. `detect_empty_after_tool` — fires when the last assistant turn
     had no content and no tool_calls, or when drydock's filler
     `Previous turn ended; awaiting your next instruction.` sits where
     the assistant response should be (retrospective equivalent).
     Replay validation: **254 fires in 244 of 400 sessions (61%)**.
     Intervention: directive telling the model its next turn MUST
     produce a tool call or a text summary — no silent hand-back.

  2. `detect_retry_after_error` — fires when an identical tool call
     follows a tool result that looks like an error. Fires at
     window=2 (earlier than the existing detector's window=3).
     Replay validation: **32 fires in 23 of 400 sessions (5.8%)**.
     Intervention: directive embedding the error snippet and naming
     the repeated tool.

**Data + artifacts written:**
  - `/data3/Deep_Noir_1/drydock_probes/mine_behavior_patterns.py`
  - `/data3/Deep_Noir_1/drydock_probes/validate_proposed_detectors.py`
  - `/data3/Deep_Noir_1/drydock_probes/results/session_behavior_stats.json`
  - `/data3/Deep_Noir_1/drydock_probes/results/session_behavior_report.md`
  - `/data3/Deep_Noir_1/drydock_probes/results/proposed_detector_validation.json`
  - `drydock/admiral/detectors_proposed.py` — in drydock tree, not
    wired; audit before enabling.
  - `tests/test_admiral_proposed.py` — **9/9 tests pass**.
    Cover empty-assistant + drydock-filler cases, healthy-sequence
    negative, retry-with-different-args negative, retry-after-success
    negative, both-detectors-with-run_proposed positive/negative.

### 11.8 What to do when you wake up

1. Read `session_behavior_report.md` — the one-page summary of what
   the 400-session mine found.
2. ~~Review `drydock/admiral/detectors_proposed.py`. If you like the two
   detectors, wire them in by adding both functions to the tuple in
   `drydock/admiral/detectors.py::run_all`.~~ **Wired in 2026-04-24.**
   Both functions are now called from `run_all`; advisory-only; existing
   `interventions.py` handles the Findings. Watch for `empty_after_tool:*`
   and `retry_after_error:*` codes in the intervention log.
3. Decide whether to escalate `EMPTY_ASSISTANT_AFTER_TOOL` from
   advisory to state-mutating. Given the 61%-of-sessions fire rate,
   dropping the empty+filler pair from context and re-rolling the
   turn (with the directive injected) is the aggressive move. But
   this breaks the "never stop, only guide" rule — your call.
4. Pause sentinels still active:
   - `/data3/drydock/research/STOP` — stops `research_babysitter.sh`
     respawn of the experimenter. Remove to resume the research loop.
   - `/data3/drydock/.pause_auto_release` — stops the 6-hour PyPI
     auto-release cron. Remove when you want to publish again.
   - `/data3/drydock/.pause_vllm_failover` — stops the 5-min
     respawn-if-down cron. **I added this sentinel tonight; the guard
     itself is a 3-line patch in `scripts/vllm_failover.sh`. Keep
     both or revert both together.** Remove sentinel to resume.
5. vLLM container is back up on the original start config (0.95
   util, 131072 ctx). Drydock TUI usable immediately.
6. For the DN path itself: park it until either (a) transformers
   lands proper MoE+compressed-tensors decompression for the
   existing AWQ-4bit checkpoint, or (b) we commit to forking vLLM
   to expose activation hooks. Neither is a this-week task; the
   probe dataset + drivers are cold-storage-ready under
   `/data3/Deep_Noir_1/drydock_probes/`.

***

