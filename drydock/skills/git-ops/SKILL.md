---
name: git-ops
description: Git operations helper. Branching, merging, rebasing, conflict resolution, history analysis.
allowed-tools: bash read_file
user-invocable: true
---

# Git Operations

Handle git tasks based on $ARGUMENTS:

## Common Operations
- **branch**: `git checkout -b feature/$0`
- **merge**: `git merge $0` with conflict resolution
- **rebase**: `git rebase $0` — fix conflicts if any
- **stash**: `git stash` / `git stash pop`
- **cherry-pick**: `git cherry-pick $0`
- **log**: `git log --oneline --graph -20`
- **blame**: `git blame $0`
- **bisect**: Binary search for the commit that introduced a bug
- **clean**: `git clean -fd` (with confirmation)

## Conflict Resolution
1. Show conflicted files: `git diff --name-only --diff-filter=U`
2. For each conflict, read the file and resolve
3. Stage resolved files: `git add <file>`
4. Continue: `git rebase --continue` or `git merge --continue`

## Rules
- NEVER force push to main/master
- Always stash before destructive operations
- Show the user what will happen before doing it
