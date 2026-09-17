"""Shared test scaffolding for the git-worktree/verifier subsystems.

`swarm`, `ratchet`, and `eratchet` all isolate work in a throwaway git tree and
score it by a verifier's pass-count, so their tests kept re-deriving the same two
helpers. They live here once:

  * ``git``       — run a git subcommand in a repo (thin subprocess wrapper).
  * ``init_repo`` — a fresh repo with one seed commit, ready for worktrees.

Import them like the other cross-test helper (``tests.test_vision_input``):

    from tests.fixtures.repos import git, init_repo
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git(args, cwd, *, check=False):
    """Run ``git <args>`` in ``cwd``; capture text output. ``check`` raises on
    non-zero (callers that assert repo state), otherwise returns the
    CompletedProcess (callers that inspect stdout/returncode themselves)."""
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=check, capture_output=True, text=True
    )


def init_repo(path, *, seed_file="README.md", seed_text="hello\n") -> str:
    """Create a git repo at ``path`` with one seed commit and return its path as
    a str. The seed gives worktree/checkpoint code a HEAD to branch from."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git(["init", "-q"], path, check=True)
    git(["config", "user.name", "t"], path, check=True)
    git(["config", "user.email", "t@t"], path, check=True)
    (path / seed_file).write_text(seed_text)
    git(["add", "-A"], path, check=True)
    git(["commit", "-qm", "init"], path, check=True)
    return str(path)
