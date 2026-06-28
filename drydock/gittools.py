"""Dedicated Version Control helpers — first-class git for the agent.

Git works through the generic Bash tool, but purpose-built git_* tools give the
model concise, structured, TRUNCATED output (a raw `git diff` can blow the
context window) and a safe commit path, without it having to remember exact git
invocations. Read ops (status/diff/log) are side-effect-free; commit is local
and reversible (push stays gated through Bash's approval modal).

All logic original to Drydock.
"""
from __future__ import annotations

import subprocess


class GitError(RuntimeError):
    """A git operation failed (not a repo, git missing, or a command error)."""


def _run_git(args: list[str], cwd: str, timeout: float = 30.0) -> str:
    """Run `git <args>` in cwd, returning stdout. Raises GitError on failure."""
    try:
        p = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise GitError("git is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after {timeout:.0f}s") from e
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        if "not a git repository" in err.lower():
            raise GitError(f"not a git repository: {cwd}")
        raise GitError(err or f"git {args[0]} failed (exit {p.returncode})")
    return p.stdout


def _truncate(text: str, max_chars: int, *, tail: bool = False) -> str:
    if len(text) <= max_chars:
        return text
    note = f"\n[... {len(text) - max_chars} chars truncated ...]\n"
    return (note + text[-max_chars:]) if tail else (text[:max_chars] + note)


def status(cwd: str) -> str:
    """Branch + concise porcelain status of the working tree."""
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd).strip()
    porcelain = _run_git(["status", "--porcelain=v1", "--branch"], cwd).strip()
    if len(porcelain.splitlines()) <= 1:  # only the "## branch" header line
        return f"On branch {branch}. Working tree clean."
    return f"On branch {branch}:\n{_truncate(porcelain, 4000)}"


def diff(cwd: str, path: str | None = None, *, staged: bool = False,
         max_chars: int = 8000) -> str:
    """Working-tree (or --staged) diff, optionally scoped to a path. Truncated."""
    args = ["diff"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", path]
    out = _run_git(args, cwd)
    if not out.strip():
        scope = " (staged)" if staged else ""
        return f"No{scope} changes" + (f" in {path}" if path else "") + "."
    # Lead with a --stat summary so the model sees the shape even when truncated.
    stat_args = ["diff", "--stat"] + (["--cached"] if staged else [])
    if path:
        stat_args += ["--", path]
    stat = _run_git(stat_args, cwd).strip()
    return f"{stat}\n\n{_truncate(out, max_chars)}"


def log(cwd: str, n: int = 10) -> str:
    """The last n commits, one line each."""
    n = max(1, min(n, 50))
    out = _run_git(["log", f"-{n}", "--oneline", "--decorate"], cwd).strip()
    return out or "No commits yet."


def commit(cwd: str, message: str, *, add_all: bool = True) -> str:
    """Stage (all, by default) and commit. Local + reversible (git reset undoes
    it). Refuses an empty message. Does NOT push."""
    message = (message or "").strip()
    if not message:
        raise GitError("a commit needs a non-empty message")
    if add_all:
        _run_git(["add", "-A"], cwd)
    # Nothing staged → say so instead of erroring opaquely.
    staged = _run_git(["diff", "--cached", "--name-only"], cwd).strip()
    if not staged:
        return "Nothing to commit — the working tree has no staged changes."
    out = _run_git(["commit", "-m", message], cwd)
    head = _run_git(["rev-parse", "--short", "HEAD"], cwd).strip()
    return f"Committed {head}:\n{out.strip()}"
