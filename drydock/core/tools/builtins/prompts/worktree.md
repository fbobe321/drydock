Git worktree tools for parallel work in isolated copies.

- `enter_worktree(branch="feature-x")` — Create isolated worktree and switch to it
- `exit_worktree(cleanup=true)` — Return to main directory, optionally remove worktree

Use worktrees when you need to make changes without affecting the main branch,
or to work on multiple things in parallel.
