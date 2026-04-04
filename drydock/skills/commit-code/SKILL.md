---
name: commit-code
description: Create a git commit with a well-crafted message. Stages changes, reviews diff, writes conventional commit.
allowed-tools: bash read_file grep
user-invocable: true
---

# Commit Workflow

1. Run `git status` and `git diff --stat` to see what changed
2. Run `git diff` to read the actual changes (limit to 200 lines)
3. Write a commit message following conventional commits:
   - `fix:` for bug fixes
   - `feat:` for new features
   - `refactor:` for refactoring
   - `docs:` for documentation
   - `test:` for test changes
   - `chore:` for maintenance
4. Stage relevant files with `git add` (specific files, not -A)
5. Create the commit with `git commit -m "message"`
6. Show `git log --oneline -3` to confirm

Rules:
- NEVER commit .env, credentials, or secrets
- Keep the first line under 72 characters
- Add a body if the change is complex
- If $ARGUMENTS is provided, use it as context for the commit message
