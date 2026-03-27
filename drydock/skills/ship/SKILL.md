---
name: ship
description: Automated shipping pipeline. Test → Review → Commit → Push → PR. Non-interactive.
user-invocable: true
allowed-tools:
  - bash
  - grep
  - read_file
  - write_file
  - search_replace
---

# Ship

Automated shipping pipeline. You are a release engineer.

## Verification Gate

**Never claim completion without fresh test evidence.**

## Pipeline

### Step 1: Pre-flight checks
```bash
git status
git diff --stat
```
- If working tree is dirty, ask what to include
- If on main/master, ask to create a branch first

### Step 2: Run tests
```bash
python3 -m pytest tests/ -q --tb=short 2>&1 | tail -20
```
- If tests fail: **STOP.** Fix the failures first.
- If no test runner found, note it and continue.

### Step 3: Review diff
- Run `/review` mentally (check for critical issues)
- If >5 files changed, confirm with user

### Step 4: Commit
- Write a clear commit message following conventional commits:
  - `feat:` for new features
  - `fix:` for bug fixes
  - `refactor:` for code changes that aren't fixes or features
  - `docs:` for documentation
  - `test:` for test changes
- Each commit should be bisectable (tests pass at every commit)

### Step 5: Push and PR
```bash
git push origin HEAD
```
- If `gh` CLI is available, create a PR:
```bash
gh pr create --title "..." --body "..."
```

## Rules

- Never force push to main/master
- Never skip tests
- Never commit secrets, tokens, or credentials
- Always include what changed and why in the commit message
