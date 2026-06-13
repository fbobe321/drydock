"""Built-in tools for DryDock v3.

Tools: Read, Write, Edit, Bash, Glob, Grep
Each tool is a function (params, config) -> str.
"""
from __future__ import annotations

import os
import re
import glob as _glob
import subprocess
from pathlib import Path

from drydock.tool_registry import ToolDef, register
from drydock.guards import (
    conflict_marker_refusal,
    has_conflict_markers,
    python_syntax_warning,
    write_warnings,
)
from drydock import bash_safety


def _resolve_path(path: str, config: dict) -> str:
    """Resolve a relative path against the session working directory.

    File tools must agree with Bash on what the working directory is —
    otherwise Write creates a file Bash can't see. Relative paths are joined
    to config['cwd']; absolute paths pass through.
    """
    cwd = config.get("cwd")
    if cwd and not os.path.isabs(path):
        return os.path.join(cwd, path)
    return path

# ── Schemas ───────────────────────────────────────────────────────────────

SCHEMAS = [
    {
        "name": "Read",
        "description": "Read a file. Returns content with line numbers. Use limit/offset for large files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to file"},
                "limit": {"type": "integer", "description": "Max lines to read"},
                "offset": {"type": "integer", "description": "Start line (0-indexed)"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Write",
        "description": "Write content to a file, creating parent directories as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": "Replace exact text in a file. old_string must match exactly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string", "description": "Exact text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Bash",
        "description": "Execute a shell command. Returns stdout+stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds (default 30)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Glob",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern e.g. **/*.py"},
                "path": {"type": "string", "description": "Base directory (default: cwd)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Search file contents with regex. Returns matching lines with file:line prefix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "Directory or file to search"},
                "include": {"type": "string", "description": "File pattern e.g. *.py"},
            },
            "required": ["pattern"],
        },
    },
]

# ── Tool implementations ──────────────────────────────────────────────────

def tool_read(params: dict, config: dict) -> str:
    fp = _resolve_path(params["file_path"], config)
    limit = params.get("limit", 2000)
    offset = params.get("offset", 0)
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        selected = lines[offset:offset + limit]
        numbered = [f"{i + offset + 1}\t{line.rstrip()}" for i, line in enumerate(selected)]
        result = "\n".join(numbered)
        if len(lines) > offset + limit:
            result += f"\n[... {len(lines) - offset - limit} more lines]"
        return result or "(empty file)"
    except FileNotFoundError:
        return f"Error: file not found: {fp}"
    except Exception as e:
        return f"Error reading {fp}: {e}"


def _record_undo(config: dict, fp: str, prev: str | None) -> None:
    """Push (path, prior-content) onto the session undo journal. prev=None
    means the file did not exist before, so undo deletes it."""
    config.setdefault("_undo", []).append((fp, prev))


def _snapshot(fp: str) -> str | None:
    """Prior content of fp, or None if it doesn't exist / can't be read."""
    p = Path(fp)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def undo_last(config: dict) -> str:
    """Revert the most recent Write/Edit. User-facing (called by /undo)."""
    journal = config.get("_undo") or []
    if not journal:
        return "Nothing to undo."
    fp, prev = journal.pop()
    try:
        if prev is None:
            Path(fp).unlink(missing_ok=True)
            return f"Undid write — removed {fp}"
        Path(fp).write_text(prev, encoding="utf-8")
        return f"Undid last change — restored {fp}"
    except OSError as e:
        return f"Could not undo {fp}: {e}"


def tool_write(params: dict, config: dict) -> str:
    fp = params.get("file_path")
    if not fp:
        return "Error: Write requires a non-empty file_path."
    fp = _resolve_path(fp, config)
    content = params.get("content", "")
    if has_conflict_markers(content):
        return conflict_marker_refusal(fp)
    prev = _snapshot(fp)
    try:
        Path(fp).parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        _record_undo(config, fp, prev)
        lines = content.count("\n") + 1
        result = f"Wrote {lines} lines to {fp}"
        # Advisory post-write checks (never block the write).
        warnings = write_warnings(fp, content)
        if warnings:
            result += "\n" + "\n".join(warnings)
        return result
    except Exception as e:
        return f"Error writing {fp}: {e}"


def _fuzzy_find(content: str, target: str) -> str | None:
    """Try to find target in content with whitespace normalization."""
    # Normalize whitespace: collapse multiple spaces/tabs, strip trailing
    def normalize(s):
        lines = s.split('\n')
        return '\n'.join(re.sub(r'[ \t]+', ' ', line.rstrip()) for line in lines)

    norm_content = normalize(content)
    norm_target = normalize(target)

    if norm_target in norm_content:
        # Find the original text that matches the normalized version
        # Search line by line
        target_lines = target.strip().split('\n')
        content_lines = content.split('\n')
        for i in range(len(content_lines) - len(target_lines) + 1):
            candidate = '\n'.join(content_lines[i:i + len(target_lines)])
            if normalize(candidate) == normalize(target.strip()):
                return candidate
    return None


def tool_edit(params: dict, config: dict) -> str:
    fp = params.get("file_path")
    if not fp:
        return "Error: Edit requires a non-empty file_path."
    fp = _resolve_path(fp, config)
    old = params.get("old_string", "")
    new = params.get("new_string", "")
    if has_conflict_markers(new):
        return conflict_marker_refusal(fp)
    try:
        content = Path(fp).read_text(encoding="utf-8")
        if old not in content:
            # Fallback 1: already-applied — the replacement is already present
            # and the original is gone. Treat as a no-op success, not an error,
            # so the model doesn't retry in a loop.
            if new and new in content:
                return (
                    f"No change: {fp} already contains the new text "
                    f"(this edit appears to have been applied already)."
                )
            # Fallback 2: fuzzy match on whitespace.
            fuzzy = _fuzzy_find(content, old)
            if fuzzy:
                old = fuzzy
            else:
                return (
                    f"Error: old_string not found in {fp}. Read the file first "
                    f"to copy the exact text (including indentation)."
                )
        count = content.count(old)
        if count > 1:
            return f"Error: old_string found {count} times in {fp}. Add more context to make it unique."
        updated = content.replace(old, new, 1)
        Path(fp).write_text(updated, encoding="utf-8")
        _record_undo(config, fp, content)
        result = f"Edited {fp}: replaced {len(old)} chars with {len(new)} chars"
        # Advisory post-edit syntax check.
        warn = python_syntax_warning(fp, updated)
        if warn:
            result += "\n" + warn
        return result
    except FileNotFoundError:
        return f"Error: file not found: {fp}. Use Write to create it."
    except Exception as e:
        return f"Error editing {fp}: {e}"


def tool_bash(params: dict, config: dict) -> str:
    cmd = params["command"]
    timeout = params.get("timeout", 30)
    reason = bash_safety.dangerous_command(cmd)
    if reason is not None:
        return bash_safety.refusal_message(cmd, reason)
    # Approval tier: sensitive-but-legitimate commands (sudo, installs, network)
    # run only after the user okays them. config["request_approval"] is the
    # UI callback; absent it (headless/tests) the command runs.
    approval_reason = bash_safety.requires_approval(cmd)
    if approval_reason and not config.get("_approve_all"):
        approver = config.get("request_approval")
        if approver is not None:
            decision = approver(cmd, approval_reason)
            if decision == "always":
                config["_approve_all"] = True
            elif decision != "allow":
                return (
                    f"REFUSED: you declined to approve this command "
                    f"({approval_reason}).\nCommand: {cmd.strip()}"
                )
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=config.get("cwd"),
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr if output else result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def tool_glob(params: dict, config: dict) -> str:
    pattern = params["pattern"]
    base = params.get("path") or config.get("cwd") or "."
    try:
        matches = sorted(_glob.glob(os.path.join(base, pattern), recursive=True))
        if not matches:
            return "(no matches)"
        if len(matches) > 200:
            return "\n".join(matches[:200]) + f"\n[... {len(matches) - 200} more]"
        return "\n".join(matches)
    except Exception as e:
        return f"Error: {e}"


def tool_grep(params: dict, config: dict) -> str:
    pattern = params["pattern"]
    path = params.get("path") or config.get("cwd") or "."
    include = params.get("include", "")
    try:
        cmd = ["grep", "-rn", "--color=never"]
        if include:
            cmd.extend(["--include", include])
        cmd.extend([pattern, path])
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        if not output:
            return "(no matches)"
        lines = output.split("\n")
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n[... {len(lines) - 100} more matches]"
        return output
    except subprocess.TimeoutExpired:
        return "Error: grep timed out"
    except Exception as e:
        return f"Error: {e}"


# ── Register all tools ────────────────────────────────────────────────────

_TOOLS = [
    ("Read", tool_read, True),
    ("Write", tool_write, False),
    ("Edit", tool_edit, False),
    ("Bash", tool_bash, False),
    ("Glob", tool_glob, True),
    ("Grep", tool_grep, True),
]

def register_all():
    for schema in SCHEMAS:
        name = schema["name"]
        func = {
            "Read": tool_read, "Write": tool_write, "Edit": tool_edit,
            "Bash": tool_bash, "Glob": tool_glob, "Grep": tool_grep,
        }[name]
        read_only = name in ("Read", "Glob", "Grep")
        register(ToolDef(name=name, schema=schema, func=func, read_only=read_only))

register_all()
