---
name: fix-issue
description: Structured bug fix workflow. Locate, hypothesize, fix, verify. Designed for SWE-bench style tasks.
allowed-tools: bash read_file grep glob search_replace write_file
user-invocable: true
---

# Fix Issue Workflow

## Phase 1: Locate
1. Grep for the class/function/error mentioned in the bug description
2. Read the relevant source file (use offset/limit, 50-100 lines)
3. Identify the root cause — which line(s) need to change

## Phase 2: Hypothesize
4. Form a hypothesis: "The bug is caused by X because Y"
5. Identify the minimal fix — usually 1-5 lines in 1 file

## Phase 3: Fix
6. Apply the fix with search_replace (exact text match)
7. If search_replace fails, re-read the file first, then retry with EXACT text
8. Read back the changed lines to verify the edit applied

## Phase 4: Verify
9. Run relevant tests if available: `python -m pytest <test_file> -x -q`
10. If tests pass, done. If tests fail, read the failure and iterate.

## Rules
- NEVER edit test files — the bug is in source code
- Keep changes minimal — no refactoring
- A wrong fix attempt is better than no fix
- Maximum 3 fix attempts before trying a different approach
- If $ARGUMENTS provided, treat it as the bug description
