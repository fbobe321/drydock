# Archived 2026-05-17 — DO NOT RESURRECT

These scripts implemented a custom HLE eval harness that drove drydock
via pexpect. The infrastructure was technically TUI-based (it spawned
the real `drydock` binary), but the user objected to the entire
*concept* of building bespoke evaluation infrastructure on the side:

> "STOP trying to create custom harnesses for evals. Improving drydock
>  is what matters. And using drydock can't be headless, you have to
>  actually use it like the user would."

The principle: **drydock improvements should come from USING drydock
as a real user would** — interactively, with the user noticing pain
points and the agent fixing them. A custom eval harness:

  1. Tempts the agent to fix harness-specific issues (timeout tuning,
     env-var-gated behavior, judge-prompt edge cases) that don't help
     real users.
  2. Introduces operational overhead (cron jobs, keepalive daemons,
     telemetry pipelines) that's parallel to the actual product.
  3. Distracts from the headline goal: make drydock better at what
     real users do, not at what an eval suite measures.

If you (future Claude) are tempted to build a new HLE-style eval
harness, STOP. Read `~/.claude/projects/-data3-drydock/memory/
feedback_no_custom_eval_harness.md` first. The user has been very
clear on this and will push back hard.

What to do instead:
  - Use the drydock TUI yourself (or watch the user use it) to find
    real bugs.
  - Read open GH issues at fbobe321/drydock for user-reported pain.
  - Fix things that affect interactive UX: error recovery, slash
    commands, tool reliability.
  - If you really need a regression-style check, write a unit test
    against the drydock library — not a long-running eval daemon.

Files preserved here for git history continuity; do not run them.
