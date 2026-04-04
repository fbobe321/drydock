---
name: context-summary
description: Summarize the current session context. What files were read, edited, what's the current state.
allowed-tools: bash read_file
user-invocable: true
---

# Context Summary

Produce a summary of the current working state:

1. **Git status**: `git status --short`
2. **Recent changes**: `git diff --stat`
3. **Recent commits**: `git log --oneline -5`
4. **Modified files**: List files changed in this session
5. **Current task**: What was the user's original request?
6. **Progress**: What's been done, what remains?

Output as a clean summary the user can quickly scan.
