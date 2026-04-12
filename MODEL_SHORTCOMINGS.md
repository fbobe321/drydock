# Gemma 4 26B-A4B Shortcomings Log

Running list of model-level limitations observed in real drydock use, with
concrete evidence. Intent: inform future LoRA fine-tuning + Deep Noir
steering datasets. Each entry is a pattern, NOT a one-off bug.

Last updated: 2026-04-12

---

## 1. Catastrophic tool-arg malformation → destructive side effects

**Pattern:** Model sends `search_replace` with `content` + `new_string` keys
but no `search` / `old_string`. Drydock's raw-code-fallback (before my fix)
overwrote entire files with fragments.

**Evidence:** ACE v1 cli.py nuked from 5171 chars → 16-char fragment. Session
`20260412_162052_ee34fefb` msg #61. Lost 120 lines of working code.

**Candidate training signal:**
- Positive: model sends `{search: "...", replace: "..."}` or proper
  `<<<<<<< SEARCH` blocks.
- Negative: model sends raw code in `content` expecting a file overwrite.

**Harness mitigation already in place:** refuse overwrites that shrink file
by >50%. Still, the model should learn to format tool calls correctly.

---

## 2. Scaffolding without wiring

**Pattern:** Model adds a new parameter to a public function's signature +
adds the implementation in the callee, but doesn't update the intermediate
call site that connects them. Tests pass for surface checks (signature
inspection) but the feature is non-functional at runtime.

**Evidence:**
- tabulate `row_count_in_header`: added to `tabulate()` signature and to
  `_format_table()` logic, but `tabulate()` never passes the new arg to
  `_format_table()`. Feature non-functional; tests pass because the test
  just checked the signature.
- Session: `session_20260412_105528_30d43609`.

**Root cause hypothesis:** Model doesn't trace execution path end-to-end.
Treats each file as independent.

**Candidate training signal:** Traces where model correctly chases a
parameter through the full call graph vs traces where it stops at surface.

---

## 3. Subtle logic bugs undiagnosed after N iterations

**Pattern:** Model produces plausible-looking code with a subtle bug.
Running tests fails. Model tries rewriting the whole file rather than
tracing the specific failure.

**Evidence:**
- **mini_db parser**: `_parse_select_body()` consumed `WHERE` as a table
  alias because alias detection only excluded `JOIN` from the list of
  SQL keywords. Fix was one line. Model made 4 iterations (~20 min)
  without finding it.
- **tool_agent log whitelist**: `if self.verbose or level in ["THINK",
  "PLAN", "TOOL"]:` — missing `"ANSWER"`. One-character fix. Model ran
  3 iterations without finding it.

**Common trait:** Bug is in a data structure / enum / list that the model
didn't reason about. It could SEE the line but didn't mentally trace
which levels were allowed.

**Candidate training signal:** Worked examples of "run the failing test,
identify the exact line that produces wrong output, trace the data flow,
fix only that line."

---

## 4. Inheritance blindness across packages

**Pattern:** When a class inherits from a parent in a different package,
the model looks for attributes/methods in the child class's file. Doesn't
think to follow the inheritance chain to the parent package.

**Evidence:**
- **Flask `request.is_xml`**: Model read `src/flask/wrappers.py` 13 times
  looking for `is_json` to pattern-match from. But `is_json` is in
  werkzeug's `Request` (parent class). Model gave up without writing
  anything. With an explicit hint in the task ("is_json is in werkzeug"),
  model solved it in 61s.

**Candidate training signal:** Traces where model correctly uses grep to
find a name in parent-class packages (`grep -r "def is_json" site-packages/`)
vs traces where it re-reads the same file.

---

## 5. Regression during targeted fixes

**Pattern:** Asked to fix failure X, model rewrites unrelated passing code
and breaks it. Advisory "don't break passing tests" in prompt doesn't help.

**Evidence:**
- **site_gen**: 4/5 tests passing. Asked to fix the 5th (title render).
  Model rewrote the template engine from scratch, lost 4 passing tests
  → 0/5. Required manual rollback.
- This happened repeatedly until I added rollback to ralph_loop.

**Harness mitigation:** Auto-rollback on score regression (snapshot before,
restore if tests go down). Critical safety net.

**Candidate training signal:** Traces with minimal targeted patches
(`search_replace` with 3-line blocks) vs traces with full-file rewrites.

---

## 6. Blindness to own stuckness

**Pattern:** Model rewrites the same file multiple times identically when
tests fail. Doesn't recognize the rewrites aren't helping; doesn't try a
different approach. When dedup block fires after 3 identical writes,
sometimes workaround by renaming the file (e.g. `feedback_new.py`).

**Evidence:**
- ACE v1 session msg #23, #25, #27: three identical writes of `feedback.py`.
  Then msg #29: renamed to `feedback_new.py` to bypass dedup.
- mini_db: 100 search_replace calls, 65 failed. Model kept trying the
  same pattern instead of switching approaches.

**Candidate training signal:** Traces where model after N failed attempts
tries: `bash` to run the failing command, `web_search` for the error,
`read_file` on a different related file, or explicitly changes approach.

---

## 7. Doesn't use web_search even when enabled

**Pattern:** Model has `web_search` tool available but doesn't reach for
it when stuck, even for standard errors that have obvious online solutions.

**Evidence:**
- mini_db session (after web tools were enabled): 100 search_replace fails,
  0 web_search calls. Model stayed in local-only failure loop.

**Mitigation attempted:** `gemma4.md` prompt now explicitly says "use
web_search when stuck 2+ iterations on same error." Need to verify it
actually happens in practice.

**Candidate training signal:** Strong — positive examples of "tried X,
failed, googled error, found answer, fixed" vs "tried X 5x, never googled."

---

## 8. Hallucinated tool names

**Pattern:** Model calls tool names that don't exist, e.g.
`ralph_repo_index`, `list_mcp_resources`, `repo_index`.

**Evidence:** mini_db v1 session called `ralph_repo_index` 5 times. Each
returned "Unknown tool" error. Model kept trying.

**Harness mitigation:** `_IGNORE_TOOLS` list silently drops known
hallucinations to reduce error spam.

**Candidate training signal:** Ground all tool calls to only names that
exist in the current tool list.

---

## 9. Empty response / thinking stall

**Pattern:** After a tool result, model generates `[thinking]` tokens but
no `content` or `tool_calls` — returns an empty assistant message. Appears
idle to the harness.

**Evidence:** ACE v2 iter 5 "idle" status at 966s with no message activity.
Also observed in earlier 5-min tier runs pre-fix.

**Harness mitigation:** `_truncate_old_tool_results` in agent_loop +
thinking-stall nudge. Partial fix — still observed in harder PRDs.

**Candidate training signal:** Eliminate empty assistant responses. Every
response should either have `content` or `tool_calls`.

---

## 10. Weak reasoning about performance / abstract tasks

**Pattern:** Optimization phase of comprehensive_loop consistently fails
("idle" status). Code review phase also struggles. These phases require
abstract reasoning about hotspots, trade-offs, priorities.

**Evidence:** comp_loop across 3 packages:
- doc_qa: 9/9 phases passed (including optimize — simple PRD)
- stock_screener: 8/9 (optimize failed — went idle)
- tool_agent: 5/9 (multiple phases failed, optimize was one)

**Candidate training signal:** Traces of optimization reasoning:
"Profile showed X is hot; it's O(n²); rewrite as O(n log n); measure."
Currently the model treats "optimize" as too vague.

---

## Harness / tooling shortcomings (non-model, but relevant)

These are harness bugs I found — fixing them made the model LOOK better:

- A. `search_replace` destructive overwrite (FIXED v2.6.67)
- B. `write_file` didn't catch bare `raise` (FIXED v2.6.69)
- C. `builder` subagent missing `web_search`/`web_fetch` (FIXED v2.6.71)
- D. `_IGNORE_TOOLS` missing common hallucinations (FIXED v2.6.71)
- E. ralph_loop false-positive baseline → wrong prompt (FIXED v2.6.71)

---

## Scoring suggested for training dataset

Label each trace-step with outcome:
- `good`: tool call succeeded + moved toward passing tests
- `regression`: broke a previously-passing test
- `wasted`: no progress on pass rate
- `stuck`: repeated same action N times with no change
- `recovered`: was stuck, used web_search/web_fetch/read_file to unblock

Fine-tune on `good` and `recovered`. Filter out `wasted` and the parts of
traces after a `regression` or `stuck` episode.
