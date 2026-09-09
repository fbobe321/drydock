# Loop control — ratchet-with-artifacts vs plain ratchet (PRE-REGISTERED)

**Written 2026-09-08, before any data.** Predictions + falsifiers are registered
machine-readably in `predictions.json` (via `drydock/predictions.py` — the artifact
under test is used to register its own trial, on purpose). The only conclusion in this
project's week-long log that needed no correction was the one pre-registered to disk;
this file is that discipline applied to the first-principles loop.

## The claim being tested
The first-principles loop was **measured dead as a prompt** (PRD §17.1): a reasoning
checklist regressed when always-on, lost to a plain retry when on-failure, and the
*longer* variant did *worse* because it was followed less. The loop was therefore rebuilt
as **artifacts** the model calls, not text it is told to obey:
- `drydock/groundtruth.py` — FACT/ASSUMPTION/UNKNOWN ledger + `next_test()` (rank unknowns
  by decision impact). Steps 2 + 6.
- `drydock/bottleneck.py` — decompose → rank components by `share × headroom`, attack the
  one limiting factor. The step between decompose and hypothesize.
- `drydock/predictions.py` — register claim + falsifier before looking. Step 8.

Exposed together as the **`Ledger` tool** (actions: add / next / verify / refute / show /
component / lever). This control asks the only question that matters: **does the artifact
form beat a plain ratchet, or does it go the way of the scaffold?**

## Design — one variable
Both arms are the *same* `ratchet_solve.sh` on the *same* tasks, budget, seeds, and model.
They differ in exactly one thing:

| | Plain arm (`ratchet_solve.sh`) | With-loop arm (`loop_ratchet.sh`) |
|---|---|---|
| ratchet, snapshots, retry, diversify-on-stall | ✓ | ✓ (identical) |
| `Ledger` tool exposed + pinned across rounds | ✗ | ✓ (persists in `/app/.drydock/*.json`, survives the snapshot) |
| extra prompt instructions | none | **none** — the tool is *available*, the model is not lectured |

The "no extra prompt" column is deliberate and is the whole point: §17.1 showed that
*telling* the model to reason harder backfires. If there is a gain it must come from the
model choosing to use a place to put its facts/bottleneck, not from a new instruction.

## Tasks, budget, seeds
- **Tasks:** the 14-task set the DPO corpus was built from (the same set the ratchet's
  28%→71% baseline delta was measured on). Tagged easy vs hard (hard = monolithic
  single-`total=1` checker, no gradient) for prediction p3.
- **Budget:** identical `MAX_ROUNDS` and `ROUND_BUDGET_S` per arm (the ratchet's current
  defaults). Equal wall-clock per task, both arms.
- **Seeds:** run each task in **both** arms; report per-task paired outcome. If fleet time
  allows, ≥2 repeats per (task, arm) to separate effect from the ratchet's known variance.

## Metrics
1. **solves** per arm (primary).
2. **median rounds-to-solve** among tasks solved by both (does it solve *faster*?).
3. **Ledger tool invocation rate** in with-loop solves (the usage guard, p2) — parsed from
   the trajectory logs.
4. solve-delta on the **hard** subset vs the **easy** subset (p3).

## Kill rule (enforced, not remembered)
The DPO v4 lesson: *"a pre-registered rule is worth only what enforces it — it ran to step
72 because nothing stopped it at 50."* So this rule is mechanical:

> **If p1's falsifier fires** — with-loop solves within ±1 of plain AND median
> rounds-to-solve not lower — **the artifacts are NOT wired into the agent loop and the
> `Ledger` tool ships behind a default-off flag or is removed.** No "try once more with a
> better nudge": that is exactly the scaffold's death spiral (more instruction, less
> behaviour).

> **If p1 survives but p2's falsifier fires** (gain without the tool being used), the gain
> is a seed/variance confound — **do not credit the loop**; re-run with more repeats before
> any claim.

> **If p1 survives and p2 holds but p3's falsifier fires**, keep only the ground-truth half
> and drop the bottleneck step from the shipped surface.

## Status
- Artifacts: built, gated (ruff ✓, pyright 0 ✓, 13+16 tests). Ledger tool: wired.
- Predictions: registered to `predictions.json` before data. ✓
- **LAUNCHED 2026-09-08 18:22** as tmux `loop_ctrl` → `run_loop_control.sh 6 900`, a
  4-task pilot (`tasks.txt`: build-cython-ext, count-dataset-tokens, largest-eigenval,
  dna-assembly), both arms paired, `MAX_ROUNDS=6 ROUND_BUDGET=900s`.
- **No fleet disruption, by construction.** The runner is registered in
  `fleet_supervisor.sh::experiment_active()`, so while it runs the supervisor skips all
  refill+keepalive; it WAITS for the `.20` lanes (`wrk_20a/20b`) to finish their in-flight
  jobs and go idle before taking the lane (nothing killed, no job orphaned — the opposite
  of the 2026-08-21 mistake), and aborts on any container the fleet still owns. The
  campaign auto-resumes on the supervisor's next tick after the pilot exits.
- Progress: `loop_control/run.log`; results accrue to `loop_control/results.csv`
  (`task,arm,solved,best,total,ledger_used`). Predictions get resolved against that CSV.
- This is a **pilot** to validate the pipeline and get a first signal; the full 14-task
  set + repeats follow if the pilot runs clean. The kill rule above is unchanged.

## Pilot #1 — VOID (2026-09-09): the tool was never exposed
The pilot completed (plain 2/4 = loop 2/4, identical rounds) and read like p1's falsifier.
It was **not** — the with-loop arm never differed from plain, because the Ledger tool was
**never offered to the model in any round**. Root cause, traced to ground truth:
- The container installs `drydock-cli==$DD_VER` from **PyPI**. The Ledger tool
  (`groundtruth`+`bottleneck`, commit `e09f229`) was added **after** PyPI **v3.1.25** was
  published (`c8752ce`) with **no version bump** — so `pip install drydock-cli==3.1.25`
  pulls a wheel with **46 tools, no Ledger** (verified against live PyPI). The local git
  tree at the same version string has 47 tools incl. Ledger. Version string ≠ artifact.
- With 47 tools and `max_tools=12`, the Ledger is trimmed out unless pinned; the pin was
  correct, but a pin on a **nonexistent** tool is a silent no-op ⇒ both arms ran identical
  plain ratchets. Proven from the host-side trajectory captures: their `tools` field
  (schemas offered per turn) contains **no `Ledger` in any round** — `ledger_usage.py`
  reports `exposed=0/N` on all pilot-1 tasks.
- The old usage probe compounded the blindness: it `docker exec`'d the container **after**
  `ratchet_solve.sh` had torn it down, so it always logged `torndown` — no signal at all.

**Fix applied (2026-09-09), the pilot re-run under it:**
1. Version bumped **3.1.25 → 3.1.26** (release-hygiene: never add a tool under an
   already-published version). A local wheel is built from that tree.
2. `run_loop_control.sh` deploys that wheel via `DD_WHEEL` to **both** arms (robust, no
   PyPI dependency; the single variable stays the pin). The running fleet keeps its own
   PyPI pin and is untouched.
3. Usage detection rewritten (`ledger_usage.py`): parses the **host-side** trajectory
   captures and reports `exposed=<rounds Ledger was offered>/<rounds>;calls=<invocations>`.
   `exposed=0/N` now hard-marks an arm VOID instead of silently reading like a real null.

This does **not** trip the kill rule — the kill rule presumes a wired tool; pilot #1 tested
nothing. Pilot #2 (relaunched 2026-09-09 08:03, tmux `loop_ctrl`) is the first real trial.
