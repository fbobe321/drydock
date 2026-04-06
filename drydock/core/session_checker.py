"""Post-session quality checker for DryDock TUI.

After each agent session, scans the conversation for common issues
and reports them to the user. This catches problems that the model
doesn't self-detect.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from drydock.core.types import LLMMessage, Role


class SessionIssue:
    def __init__(self, severity: str, category: str, message: str):
        self.severity = severity  # "error", "warning", "info"
        self.category = category
        self.message = message

    def __str__(self):
        icons = {"error": "✗", "warning": "⚠", "info": "ℹ"}
        return f"{icons.get(self.severity, '?')} [{self.category}] {self.message}"


def check_session(messages: list, has_made_edit: bool = False) -> list[SessionIssue]:
    """Check a completed session for quality issues.

    Returns a list of issues found. Empty list = clean session.
    """
    issues = []

    if not messages:
        return issues

    # 1. Unknown tool errors
    unknown_tools = []
    for msg in messages:
        content = str(getattr(msg, 'content', '') or '')
        if "Unknown tool" in content:
            match = re.search(r"Unknown tool '(\w+)'", content)
            if match:
                unknown_tools.append(match.group(1))
    if unknown_tools:
        tools = ', '.join(set(unknown_tools))
        issues.append(SessionIssue(
            "warning", "tool_error",
            f"Model tried to call unavailable tools: {tools}"
        ))

    # 2. search_replace errors
    sr_errors = 0
    for msg in messages:
        content = str(getattr(msg, 'content', '') or '')
        if "search_replace" in content and ("error" in content.lower() or "not found" in content.lower()):
            sr_errors += 1
    if sr_errors > 2:
        issues.append(SessionIssue(
            "warning", "edit_failures",
            f"search_replace failed {sr_errors} times — model may not have applied intended edits"
        ))

    # 3. Same file written too many times
    write_paths = []
    for msg in messages:
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tc in (msg.tool_calls or []):
                if hasattr(tc, 'function') and tc.function:
                    if tc.function.name == "write_file":
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                            path = args.get("path", "")
                            if path:
                                write_paths.append(path)
                        except (json.JSONDecodeError, AttributeError):
                            pass
    if write_paths:
        counts = Counter(write_paths)
        for path, count in counts.most_common(3):
            if count > 3:
                issues.append(SessionIssue(
                    "warning", "write_loop",
                    f"'{path}' was written {count} times — possible loop"
                ))

    # 4. Text repetition in model responses
    for msg in messages:
        content = str(getattr(msg, 'content', '') or '')
        if len(content) > 300:
            sentences = [s.strip() for s in content.split('.') if len(s.strip()) > 30]
            if sentences:
                counts = Counter(sentences)
                most, count = counts.most_common(1)[0]
                if count >= 3:
                    issues.append(SessionIssue(
                        "warning", "text_loop",
                        f"Model repeated the same sentence {count} times"
                    ))
                    break  # Only report once

    # 5. "Please let me know" / asking for confirmation
    ask_count = 0
    for msg in messages:
        content = str(getattr(msg, 'content', '') or '').lower()
        if any(phrase in content for phrase in [
            "please let me know", "shall i", "would you like",
            "ready to begin", "please provide", "let me know if",
        ]):
            ask_count += 1
    if ask_count > 1:
        issues.append(SessionIssue(
            "info", "confirmation_asking",
            f"Model asked for confirmation {ask_count} times instead of acting"
        ))

    # 6. API errors
    api_errors = 0
    for msg in messages:
        content = str(getattr(msg, 'content', '') or '')
        if "API error" in content or "400 Bad Request" in content:
            api_errors += 1
    if api_errors > 0:
        issues.append(SessionIssue(
            "error", "api_errors",
            f"{api_errors} API errors occurred during the session"
        ))

    # 7. No edits — only flag if the user explicitly asked to build/fix/create something
    # Questions, exploration, and running code don't need edits
    if not has_made_edit and len(messages) > 10:
        user_msgs = [str(getattr(m, 'content', '') or '').lower() for m in messages
                     if hasattr(m, 'role') and str(getattr(m, 'role', '')) == 'user']
        build_words = ["build", "create", "fix", "implement", "write", "add", "modify", "update", "edit"]
        user_asked_for_edit = any(
            any(w in msg for w in build_words)
            for msg in user_msgs
        )
        if user_asked_for_edit:
            issues.append(SessionIssue(
                "info", "no_edits",
                "No file changes were made — the model may not have completed the task"
            ))

    return issues


def format_issues(issues: list[SessionIssue]) -> str:
    """Format issues for display in the TUI."""
    if not issues:
        return ""

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    infos = [i for i in issues if i.severity == "info"]

    lines = ["", "Session Quality Check:"]
    for issue in errors + warnings + infos:
        lines.append(f"  {issue}")

    return "\n".join(lines)
