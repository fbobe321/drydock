# PRD: Admiral + Drydock (Adaptive Agent Orchestration System)

## 1. Overview
* **Execution Harness:** Drydock
* **Orchestration & Meta-Learning Layer:** Admiral
* **Vision:** Create an observable, self-tuning LLM harness. Drydock handles the deterministic execution of agentic tasks (tools, loops, file ops), while Admiral operates as an out-of-band supervisor that detects failure patterns, dynamically updates execution parameters, and controls the system's learning rate to prevent behavioral degradation.

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
  `~/.vibe/logs/admiral_history.log`.
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

### Phase 2 — DESIGNED (target v2.6.139+)

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

### Phase 3 — IDEAS (not started)

* **Learning-rate scheduler (PRD §4.1.D)** — cold start accepts many
  tweaks, stable mode accepts few. Implement by tracking how many
  interventions the user manually undid via slash-commands.
* **Session vs Global memory (PRD §6)** — session state wiped per run;
  global state only promoted if a fix worked across ≥2 sessions.
* **TUI widget** — a collapsible Admiral panel showing recent findings
  and their outcomes, so the user can audit the supervisor.

## 9. Continuity Notes (if this session is lost)

If you restart mid-Phase-2:
* **What ships:** `drydock/admiral/` (Phase 1, working) + PRD (this
  file). Check `git log --oneline -20` — Phase 1 commit should name
  "Admiral Phase 1".
* **What's missing:** `drydock/admiral/llm_analyzer.py` (local LLM)
  and `drydock/admiral/opus_escalator.py` (Claude Code fallback).
  Neither exists yet — start them from the Phase 2 section above.
* **Audit log:** `~/.vibe/logs/admiral_history.log` shows every
  intervention Admiral has applied in real user sessions.
* **User directives recorded so far in this session:**
  - Architecture: option C (in-process Textual worker).
  - Approval: auto-apply, no Y/N dialog.
  - Phase 2: Admiral uses local LLM first; escalates to Claude Code
    (Opus) when stumped.
  - Keep updating this PRD as work progresses so a session loss
    doesn't cost context.

***

