# DRYDOCK.md — harness instructions

Auto-loaded into the system prompt when DryDock runs in this directory.
Equivalent of `CLAUDE.md` for Claude Code: the per-project instructions
file the model reads on every session. Keep it short — every byte here
costs context budget on every turn.

## What DryDock is

A local-first CLI coding agent with a TUI. Talks to any
OpenAI-compatible local model server (vLLM, llama.cpp, Ollama,
LM Studio). Production target: Gemma 4 26B-A4B-it on a 2-GPU
workstation. The agent loop, classifier, Curiosity Layer, GraphRAG
hook, Admiral supervisor, and tool plugins all live in `drydock/core/`.

The harness is the product. When something goes wrong, fix the
harness — don't work around it in a script.

## Four core principles (your default posture)

1. Don't assume. Don't hide confusion. Surface tradeoffs.
2. Minimum code that solves the problem. Nothing speculative.
3. Touch only what you must. Clean up only your own mess.
4. Define success criteria. Loop until verified.

## Tool inventory — when to use each

**Always-available reads:** `read_file`, `glob`, `grep`, `retrieve` (GraphRAG),
`web_search`, `web_fetch`. Auto-approved.

**Writes (gate-able):** `write_file`, `search_replace`, `bash`, `notebook_edit`.

**Direct built-ins for transformer weaknesses (USE THESE — don't compute in your head):**

- `math(expression="...")` — exact arithmetic via Python stdlib (`math.factorial(20)`,
  `math.comb(50, 5)`, `Fraction(1,3) + Fraction(1,6)`,
  `statistics.mean([...])`). USE for any non-trivial number.
- `count(pattern="...", text=... OR path=..., mode=...)` — exact substring /
  regex / lines / words / chars / bytes counter. USE instead of estimating
  "how many".
- `memory(op="save"|"recall"|"list_keys"|"forget"|"stats", ...)` — persistent
  cross-session notes at `~/.drydock/agent_memory/notes.jsonl`. SAVE
  per-project patterns and decisions; RECALL them next session.
- `verify(criterion="...", command="...", expect="...", expect_mode=...)` —
  programmatic success check after a change. Operationalizes principle #4
  ("Loop until verified").

**Curiosity Layer (PRD §5.7) — your default posture is "investigate, then assert":**

- If the user message names an unfamiliar entity (paper, library, API,
  identifier), your FIRST tool call is `retrieve(query="<the term>")`.
  Not text. Not web_search. Retrieve.
- "I think it's X" is not an answer when retrieve costs one tool call.
- When retrieved evidence contradicts what you were about to say,
  prefer the evidence and say so. Don't quietly drop the contradiction.

**Subagent delegation:** Only for genuinely large tasks (9+ files, multiple
subdirs). For 1-8 files BUILD INLINE — wasted delegation costs 60-90s of
extra context loading.

## Critical constraints

- **Loop detection is advisory.** It NEVER stops you — only nudges and
  prunes duplicate calls. The only hard stop is `MAX_TOOL_TURNS = 200`.
- **No `--no-verify` git commits, no `pkill drydock`** (kills the user's TUI).
- **TUI only.** Headless mode was removed deliberately. All testing
  goes through the real TUI via `pexpect` (see `scripts/shakedown.py`).
- **Don't `git push` from this repo** — local and origin diverge by design;
  `auto_release.sh` rebuilds and pushes every 6h. Direct edits to
  `site-packages/` get overwritten at the next cron tick.
- **Match exact CLI specs in PRDs.** Every subcommand listed must have a
  working handler — not just argparse registration.
- **Stub classes are forbidden.** Never write
  `class X: def method(self): pass` inline to silence ModuleNotFoundError.
  Write the real class in its own file and import it.
- **`--help` is not a test.** A package that `--help`s successfully can
  have broken imports in every other module. Real test = a
  `functional_tests.sh` that exercises actual feature commands and
  checks real outputs.

## File map (only the load-bearing pieces)

```
drydock/
├── core/
│   ├── agent_loop.py          ← THE main loop. Loop detection, message
│   │                             ordering, Curiosity hooks, MTP dispatch
│   ├── system_prompt.py       ← Loads DRYDOCK.md + gemma4.md/cli.md
│   ├── tools/
│   │   ├── manager.py         ← Tool auto-discovery (any non-_ .py here
│   │   │                         is picked up)
│   │   └── builtins/          ← read_file, write_file, grep, glob, bash,
│   │                            retrieve, math, count, memory, verify, ...
│   ├── classifier/            ← Failure → 5-bucket dispatch (harness /
│   │                            retrieval / steering / model_prior /
│   │                            ambiguous_input)
│   └── prompts/
│       ├── gemma4.md          ← Compact (50 lines) prompt for Gemma 4
│       └── cli.md             ← Full prompt for stronger models
├── curiosity/                 ← SOVEREIGN_PRD §5.7 — gap_detector,
│                                surprise scorer, JSONL queue, CLI
├── graphrag/                  ← AST symbol indexer + TF-IDF retriever
├── steering/                  ← Deep Noir vector framework (scaffolding)
└── cli/textual_ui/app.py      ← TUI app + slash commands

scripts/
├── auto_release.sh            ← Cron 0,6,12,18 CDT — bumps version, builds
│                                wheel, twine upload, GitHub push
├── deploy_to_github.sh        ← Cron 04:00 CDT — full test gate then sync
├── autonomous_review.sh       ← Cron */30 — Claude Code drains classifier
│                                + curiosity queues, ships fixes
├── shakedown.py               ← The honest test harness (drives real TUI)
└── bench_inference.py         ← Throughput benchmark vs localhost:8000
```

## Workflows

**Building from a PRD:**

1. Read the spec.
2. `write_file` each file. Start with `__init__.py` and `__main__.py`.
3. Verify package imports: `bash` → `python3 -m <pkg> --help`.
4. Run a real test: `bash` → exercise actual subcommands; check outputs
   match the PRD's examples.
5. If the PRD has a `functional_tests.sh`, run THAT — not just `--help`.

**Fixing a bug:**

1. `grep` for the symbol mentioned.
2. `read_file` the source.
3. `search_replace` minimal change.
4. `verify(criterion="...", command="pytest ...", expect_mode="exit_code", expect="0")`.

**Adding a feature to drydock itself (in this repo):**

1. `git status` first — never destroy uncommitted work.
2. Make the change, run the relevant test file (`pytest tests/test_X.py -q`).
3. Commit; let `auto_release.sh` (next 6h tick) ship to PyPI + GitHub.
4. Don't `git push` — see Constraints.

## When you stop

A turn ends when:

- TODO mode (multi-step request like "build the package"): every item done.
- SIMPLE mode (single ask): exactly that, then stop. Don't invent
  follow-up work the user didn't ask for.

NEVER ask "would you like me to proceed". Just do it.
