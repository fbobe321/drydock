---
name: pr-review
description: Review a pull request. Fetches diff, analyzes changes, checks for bugs, security issues, and style.
allowed-tools: bash read_file grep glob
user-invocable: true
---

# PR Review Workflow

1. Get the PR diff: `git diff main...HEAD` (or `!`git diff main...HEAD``)
2. List changed files: `git diff --name-only main...HEAD`
3. For each changed file:
   a. Read the full diff for that file
   b. Check for: bugs, security issues, performance problems, missing error handling
   c. Check for: style consistency, naming conventions, dead code
4. Produce a structured review:

## Review Output Format
### Critical Issues (must fix)
- [file:line] Description of issue

### Suggestions (should fix)
- [file:line] Description of improvement

### Nits (optional)
- [file:line] Minor style/preference items

### Summary
- Overall assessment: APPROVE / REQUEST_CHANGES / COMMENT
- One paragraph summary of the changes

If $0 is a PR number, fetch it with: `gh pr diff $0`
