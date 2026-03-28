---
name: batch
description: Apply the same change across many files. Generates a plan, then processes each file.
user-invocable: true
allowed-tools:
  - bash
  - grep
  - read_file
  - search_replace
  - write_file
  - task_create
  - task_update
---

# Batch Changes

Apply the same change pattern across multiple files.

## Workflow

1. **Identify scope**: Use `grep` or `glob` to find all files that need the change
2. **Create plan**: List each file and the specific change needed
3. **Track with tasks**: Create a task for each file change
4. **Execute**: Process each file one at a time
5. **Verify**: Read back each changed file to confirm

## Rules

- Process ONE file at a time — do not batch edits
- Create a task_create for each file before editing
- Mark task_update as completed after each file
- If a file edit fails, skip it and note the failure
- After all files, report: X/Y files changed successfully

## Example

User: "Add type hints to all function parameters in src/"

1. `grep -r "def " src/ --include="*.py"` → find all functions
2. For each file with untyped params:
   - `task_create`: "Add type hints to src/utils.py"
   - `read_file` the function
   - `search_replace` to add types
   - `task_update`: completed
3. Summary: "Added type hints to 12/15 files (3 skipped: complex generics)"
