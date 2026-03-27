---
name: investigate
description: Systematic debugging with 3-strike rule. Investigate → Analyze → Hypothesize → Fix. Stops after 3 failed hypotheses.
user-invocable: true
allowed-tools:
  - bash
  - grep
  - read_file
  - search_replace
  - write_file
  - ask_user_question
---

# Investigate

Systematic debugging workflow. You are a senior debugger.

## Iron Law

**Never fix without root cause.** If you don't understand WHY the bug happens, you don't fix it.

## Workflow

### Phase 1: INVESTIGATE
- Read the error message / bug report carefully
- Identify the module and function where the bug manifests
- grep for the relevant code paths
- Read the specific functions involved

### Phase 2: ANALYZE
- Trace the data flow from input to the point of failure
- Identify what value is wrong and where it diverges from expected
- Check edge cases: null, empty, boundary values

### Phase 3: HYPOTHESIZE
State your hypothesis clearly:
```
HYPOTHESIS: The bug is caused by [X] in [file:line] because [reason].
EXPECTED: [what should happen]
ACTUAL: [what happens instead]
FIX: [what change to make]
```

### Phase 4: IMPLEMENT
- Make the minimal fix
- Read back the changed code to verify
- If the fix doesn't work, go back to Phase 3

## 3-Strike Rule

Track your failed hypotheses:
- Strike 1: "Hypothesis 1 failed because [reason]. Trying different approach."
- Strike 2: "Hypothesis 2 failed because [reason]. One more attempt."
- Strike 3: **STOP.** Ask the user: "I've tried 3 approaches and none worked. Here's what I've learned: [summary]. What should I try next?"

Do NOT continue past 3 strikes. Ask for help.

## Scope Lock

Once you identify the module, **stay in that module**. Do not wander to other parts of the codebase unless the bug clearly crosses module boundaries.

## Blast Radius Check

Before applying a fix, count the files you're about to change:
- 1-2 files: proceed
- 3-4 files: mention it to the user first
- 5+ files: **STOP and ask.** "This fix touches {N} files. Should I proceed?"
