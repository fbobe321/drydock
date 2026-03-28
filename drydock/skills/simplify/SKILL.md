---
name: simplify
description: Review changed code for reuse, quality, and efficiency. Fix issues found.
user-invocable: true
allowed-tools:
  - bash
  - grep
  - read_file
  - search_replace
---

# Simplify

Review recent changes for code quality and fix issues.

## Three-Pass Review

### Pass 1: Duplication
- Check if any new code duplicates existing patterns
- Look for copy-pasted blocks that should be a shared function
- Search for similar patterns: `grep -r "pattern" --include="*.py"`

### Pass 2: Complexity
- Functions over 30 lines → should they be split?
- Nesting deeper than 3 levels → use early returns
- Long parameter lists → use a config object or dataclass

### Pass 3: Correctness
- Missing error handling on I/O operations
- Unclosed resources (files, connections)
- Missing null/empty checks on external input
- Type mismatches

## Rules

- Fix mechanical issues immediately (duplication, style)
- Ask about judgment calls (architecture, naming)
- Keep changes minimal — don't refactor beyond what's needed
- Run `git diff` first to see what changed
