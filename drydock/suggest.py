"""Recommended next command — a single dimmed hint shown after each turn (à la
Claude Code's ghost suggestion). Pure logic so it's testable; the TUI renders it.
"""
from __future__ import annotations


def suggest_next_command(
    *,
    ctx_pct: int,
    wrote_files: bool,
    ran_bash: bool,
    had_error: bool,
    in_git: bool,
    plan_remaining: bool,
) -> str | None:
    """The single most useful next step given what the last turn did, or None to
    show nothing (better no hint than a noisy one). Order = priority."""
    if ctx_pct >= 78:
        return "/compact"          # context nearly full — shrink it before continuing
    if had_error:
        return "fix the error above"
    if plan_remaining:
        return "continue"          # the model left plan steps unfinished
    if wrote_files and in_git:
        return "git diff"          # review what changed
    if wrote_files:
        return "run the tests"
    if ran_bash:
        return None                # ran a command but nothing else obvious — no clutter
    return None
