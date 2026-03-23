When fixing bugs in open-source repositories (SWE-bench mode):

WORKFLOW:
1. grep for the class/function name from the bug report — focus on SOURCE files, not test files
2. read_file the relevant function (use offset/limit for 50-100 lines around the target)
3. Understand the root cause — what behavior is wrong and what should it be?
4. search_replace to make the minimal fix (1-5 lines)
5. read_file the changed area to verify the edit landed correctly

CRITICAL RULES:
- When grep returns results in both source and test files, ALWAYS look at source files first
- Most bugs need changes to ONE file only — don't over-engineer
- Keep search_replace old_str to 1-5 lines — just the lines being changed plus unique context
- If search_replace fails with "not found", re-read the file to get the EXACT current text
- Do NOT run tests — the test runner handles that separately
- After your edit, verify by reading back the modified lines

COMMON PATTERNS:
- Missing edge case: add a condition check before the problematic operation
- Wrong return value: trace the data flow to find where the value diverges
- Missing import: grep for where the symbol is defined, add the import
- Off-by-one: check loop bounds and slice indices
