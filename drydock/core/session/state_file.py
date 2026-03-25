"""Structured state file for cross-session memory.

Maintains a `.drydock/state.md` file in the project directory that tracks:
- Current task and status
- Files modified in this session
- Key decisions made
- Blockers and next steps

Max 100 lines. Loaded on session start, updated on session end.
Enables "resume where I left off" workflows.

Inspired by GSD's STATE.md concept.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_LINES = 100
_STATE_FILENAME = "state.md"


def _get_state_path(project_dir: Path | None = None) -> Path:
    """Get the path to the state file."""
    base = project_dir or Path.cwd()
    return base / ".drydock" / _STATE_FILENAME


def load_state(project_dir: Path | None = None) -> str:
    """Load the state file content, or empty string if it doesn't exist."""
    path = _get_state_path(project_dir)
    try:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            logger.info("Loaded session state from %s (%d lines)", path, content.count("\n"))
            return content
    except (OSError, PermissionError) as e:
        logger.warning("Could not read state file %s: %s", path, e)
    return ""


def save_state(
    project_dir: Path | None = None,
    *,
    current_task: str = "",
    files_modified: list[str] | None = None,
    decisions: list[str] | None = None,
    blockers: list[str] | None = None,
    next_steps: list[str] | None = None,
    custom_notes: str = "",
) -> None:
    """Save the state file with structured session information."""
    path = _get_state_path(project_dir)

    lines: list[str] = [
        f"# DryDock Session State",
        f"_Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
    ]

    if current_task:
        lines.extend([f"## Current Task", current_task, ""])

    if files_modified:
        lines.append("## Files Modified")
        for f in files_modified[-20:]:  # Keep last 20
            lines.append(f"- `{f}`")
        lines.append("")

    if decisions:
        lines.append("## Decisions")
        for d in decisions[-10:]:  # Keep last 10
            lines.append(f"- {d}")
        lines.append("")

    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")

    if next_steps:
        lines.append("## Next Steps")
        for n in next_steps:
            lines.append(f"- {n}")
        lines.append("")

    if custom_notes:
        lines.extend(["## Notes", custom_notes, ""])

    # Enforce max lines
    content = "\n".join(lines[:_MAX_LINES])

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info("Saved session state to %s", path)
    except (OSError, PermissionError) as e:
        logger.warning("Could not write state file %s: %s", path, e)


def update_state_files_modified(
    project_dir: Path | None = None,
    files: list[str] | None = None,
) -> None:
    """Quick update: append modified files to the state without rewriting everything."""
    if not files:
        return

    path = _get_state_path(project_dir)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""

        if "## Files Modified" not in existing:
            # Append a new section
            existing += "\n## Files Modified\n"

        for f in files:
            entry = f"- `{f}`"
            if entry not in existing:
                existing += f"{entry}\n"

        # Enforce max lines
        lines = existing.split("\n")[:_MAX_LINES]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except (OSError, PermissionError):
        pass  # Non-critical


def clear_state(project_dir: Path | None = None) -> None:
    """Remove the state file."""
    path = _get_state_path(project_dir)
    try:
        if path.exists():
            path.unlink()
            logger.info("Cleared session state at %s", path)
    except (OSError, PermissionError):
        pass
