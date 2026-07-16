"""Structured tool results (Agent-Buildout PRD, section 7.4 / "structured tool
results"): a tool's output is not immediately reduced to a single text string —
status, diagnostics, retryability, and timing stay available to the controller for
loop/progress detection, verification, and recovery.

Incremental by design: tools still return plain strings (no rewrite), and
tool_registry.execute() still returns a string for full backward-compat. The new
execute_structured() WRAPS that string into a ToolResult, classifying status/error/
retryability from the text + timing. Logic original to Drydock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """A tool call's outcome with structure the controller can reason about."""
    name: str
    status: str = "ok"            # "ok" | "error" | "not_found"
    text: str = ""               # the human/model-facing string (backward-compat)
    changed_state: bool = False   # did it mutate files/env (Write/Edit/mutating Bash)?
    retryable: bool = False       # transient failure worth retrying?
    error_code: str | None = None  # coarse classification when status == error
    exit_code: int | None = None   # for Bash, if present
    duration_ms: int = 0
    diagnostics: list[str] = field(default_factory=list)

    def to_event(self) -> dict:
        """Compact dict for the event log (no big blobs)."""
        return {
            "name": self.name, "status": self.status,
            "changed_state": self.changed_state, "retryable": self.retryable,
            "error_code": self.error_code, "exit_code": self.exit_code,
            "duration_ms": self.duration_ms, "result_chars": len(self.text),
        }


_EXIT = re.compile(r"\[exit code:\s*(-?\d+)\]")
_MUTATING_BASH = re.compile(
    # Word-y mutating commands need the trailing \b; output redirection (>, >>)
    # must NOT — `echo hi > file` has no word char right after '>', so a shared
    # \b silently missed every space-separated redirect (found via a bench trace
    # where 38 heredoc/redirect writes all scored as "nothing changed").
    r"(?:^|[\s;&|])(?:rm|mv|cp|mkdir|touch|tee|sed\s+-i|dd|chmod|chown|ln|"
    r"git\s+(?:commit|add|apply|checkout|reset|rm|mv)|pip\s+install|npm\s+i|"
    r"apt|make(?:\s|$)|cargo\s+build)\b"
    r"|(?:^|[\s;&|])>>?", re.I,
)
# Error phrases that usually mean "try again might work" (transient).
_RETRYABLE = re.compile(
    r"\b(timed out|timeout|connection (refused|reset|error)|temporarily|"
    r"try again|rate limit|EAGAIN|ETIMEDOUT|network is unreachable|"
    r"resource temporarily unavailable|503|502|429)\b", re.I,
)


def _classify_error(text: str) -> tuple[str, bool]:
    """(error_code, retryable) for an error result string."""
    t = text.lower()
    if _RETRYABLE.search(text):
        return "transient", True
    if "not found" in t or "no such file" in t or "command not found" in t:
        return "not_found", False
    if "permission denied" in t:
        return "permission", False
    if "syntax error" in t or "invalid" in t:
        return "invalid_input", False
    if "timed out" in t:
        return "timeout", True
    return "error", False


def classify(name: str, params: dict, text: str, duration_ms: int) -> ToolResult:
    """Build a ToolResult from a tool's (name, params, string result). Never raises."""
    text = text if isinstance(text, str) else str(text or "")
    r = ToolResult(name=name, text=text, duration_ms=max(0, int(duration_ms)))

    m = _EXIT.search(text)
    if m:
        try:
            r.exit_code = int(m.group(1))
        except ValueError:
            pass

    # Status: an "Error…" prefixed string, a "tool not found", or a non-zero Bash
    # exit means the call did not succeed. Otherwise ok.
    lowered = text.lstrip()[:60].lower()
    if lowered.startswith("error: tool") and "not found" in text.lower():
        r.status, r.error_code = "not_found", "not_found"
    elif lowered.startswith("error"):
        r.status = "error"
        r.error_code, r.retryable = _classify_error(text)
    elif r.exit_code not in (None, 0):
        r.status = "error"
        r.error_code, r.retryable = _classify_error(text)

    # State change: Write/Edit always mutate; Bash only for mutating commands.
    if name in ("Write", "Edit", "GitCommit", "BuildKnowledge", "GraphAdd"):
        r.changed_state = r.status == "ok"
    elif name == "Bash":
        cmd = (params or {}).get("command", "") if isinstance(params, dict) else ""
        r.changed_state = bool(_MUTATING_BASH.search(cmd)) and r.status == "ok"

    return r
