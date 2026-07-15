"""Tool effect classification + approval policy (PRD Epic H).

Every tool has an effect — reading a file is not the same as opening a GitHub
issue or dropping a database. The runtime classifies each tool and applies a
policy: read-only and local edits run automatically; external mutations,
credential access and destructive actions require the operator's approval before
they run; credential-access results get sensitive fields redacted before the
model (or the event log) ever sees them.

This generalises the existing Bash command-level approval (bash_safety) to ALL
tools — the MCP tools especially, where "create_issue" or "query the prod DB"
must not fire without a human okay. Advisory-compatible: when there's no approval
UI wired (tests), the policy resolves but nothing blocks. All logic original to
Drydock.
"""
from __future__ import annotations

from enum import Enum


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_MUTATION = "external_mutation"
    CREDENTIAL_ACCESS = "credential_access"
    DESTRUCTIVE = "destructive"
    LONG_RUNNING = "long_running"


# Default policy per effect (PRD §12): automatic vs approval-required.
_DEFAULT_POLICY: dict[ToolEffect, str] = {
    ToolEffect.READ_ONLY: "automatic",
    ToolEffect.LOCAL_MUTATION: "automatic",
    ToolEffect.EXTERNAL_MUTATION: "approval",
    ToolEffect.CREDENTIAL_ACCESS: "approval",
    ToolEffect.DESTRUCTIVE: "approval",
    ToolEffect.LONG_RUNNING: "approval",
}

# Name-based classification for tools that don't declare an effect. Built-in
# read-only tools are detected from their registry flag; these cover the rest.
_LOCAL_MUTATION_NAMES = frozenset({
    "Write", "Edit", "GitCommit", "GraphAdd", "BuildKnowledge", "StigSet",
})
_EXTERNAL_READ_NAMES = frozenset({"WebSearch", "WebFetch"})


def effect_of(name: str, read_only: bool = False, declared: ToolEffect | None = None) -> ToolEffect:
    """Classify a tool's effect. An explicitly declared effect always wins; else
    derive it: MCP tools (mcp__server__tool) are external mutations by default
    (the server decides otherwise via trust config), read-only tools are
    READ_ONLY, known local writers are LOCAL_MUTATION, everything else is
    LOCAL_MUTATION (the safe-but-automatic middle)."""
    if declared is not None:
        return declared
    if name.startswith("mcp__"):
        return ToolEffect.EXTERNAL_MUTATION
    if read_only or name in _EXTERNAL_READ_NAMES:
        return ToolEffect.READ_ONLY
    if name in _LOCAL_MUTATION_NAMES:
        return ToolEffect.LOCAL_MUTATION
    return ToolEffect.LOCAL_MUTATION


def policy_for(effect: ToolEffect, config: dict | None = None) -> str:
    """Return 'automatic' or 'approval' for this effect, honoring any per-effect
    override in config['tool_policy'] (e.g. {'external_mutation': 'automatic'})."""
    overrides = (config or {}).get("tool_policy") or {}
    key = effect.value if isinstance(effect, ToolEffect) else str(effect)
    val = overrides.get(key)
    if val in ("automatic", "approval"):
        return val
    return _DEFAULT_POLICY.get(effect, "automatic")


def requires_approval(name: str, *, read_only: bool = False,
                      declared: ToolEffect | None = None, config: dict | None = None) -> bool:
    """Whether this tool call needs operator approval before running."""
    return policy_for(effect_of(name, read_only, declared), config) == "approval"


def redact(structured, fields) -> object:
    """Return a copy of a structured result with `fields` masked wherever they
    appear (top level or nested). For CREDENTIAL_ACCESS tools, so secrets never
    reach the model or the event log. Non-dict/list values pass through."""
    secret = set(fields or ())
    if not secret:
        return structured
    if isinstance(structured, dict):
        return {k: ("***" if k in secret else redact(v, secret)) for k, v in structured.items()}
    if isinstance(structured, list):
        return [redact(v, secret) for v in structured]
    return structured
