---
name: refactor
description: Structured code refactoring. Identify patterns, plan changes, apply safely, verify.
allowed-tools: bash read_file grep glob search_replace write_file
user-invocable: true
---

# Refactoring Workflow

## Phase 1: Understand
1. Read the code to refactor (specified in $ARGUMENTS or recent changes)
2. Identify the refactoring type:
   - Extract function/method
   - Rename variable/function/class
   - Simplify conditionals
   - Remove duplication
   - Split large function
   - Move code to better location

## Phase 2: Plan
3. List every file that will change
4. For each file, describe the specific change
5. Identify risks: what could break?

## Phase 3: Apply
6. Make changes one file at a time with search_replace
7. After each change, verify syntax: `python -c "import ast; ast.parse(open('file').read())"`

## Phase 4: Verify
8. Run tests if available
9. Check imports still resolve
10. Verify no functionality changed (refactor = same behavior, better structure)

## Rules
- One refactoring at a time — don't combine multiple refactors
- Preserve all existing behavior
- Run tests after each change
