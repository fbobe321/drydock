---
name: diff-review
description: Show and analyze uncommitted changes. Summarize what changed and why.
allowed-tools: bash read_file
user-invocable: true
---

# Diff Review

1. Show uncommitted changes: `git diff --stat`
2. Show the full diff: `git diff`
3. For each changed file, analyze:
   - What was the intent of the change?
   - Is the change correct and complete?
   - Any issues or improvements?
4. Provide a summary suitable for a commit message
