"""Dynamic tool selection (PRD Epic F) — show the model only the tools that are
relevant to the task and phase, capped at a sane maximum.

A local model handed 26 tools every turn wastes context and mis-fires; the PRD
asks for no more than a configured maximum (default 12), the core coding tools
always present, read/search tools preferred while exploring, and specialised
tools (git, web, compliance, knowledge, vision) surfaced only when the task
actually calls for them.

Conservative by design: the core coding loop (Read/Write/Edit/Bash/Glob/Grep/
todo) is NEVER trimmed, and when the total already fits under the cap every tool
is kept — selection only ever *drops the least-relevant specialised tools when
over the limit*, so it can't hide something the model needs for a normal edit.
All logic original to Drydock.
"""
from __future__ import annotations

DEFAULT_MAX_TOOLS = 12

# The coding loop — always available regardless of phase or task text.
CORE_TOOLS = frozenset({"Read", "Write", "Edit", "Bash", "Glob", "Grep", "todo"})

# Read/search tools, preferred while understanding/discovering.
READ_SEARCH_TOOLS = frozenset({
    "Read", "Grep", "Glob", "GitStatus", "GitDiff", "GitLog", "Consult",
    "Knowledge", "GraphQuery", "WebSearch", "WebFetch",
})

# Specialised tool families gated on task relevance: (tool names, trigger keywords).
_FAMILIES: list[tuple[frozenset[str], tuple[str, ...]]] = [
    (frozenset({"GitStatus", "GitDiff", "GitLog", "GitCommit"}),
     ("git", "commit", "branch", "diff", "merge", "rebase", "stage", "repository")),
    (frozenset({"WebSearch", "WebFetch"}),
     ("web", "http", "url", "online", "search the", "documentation", "download", "fetch")),
    (frozenset({"StigRules", "StigRule", "StigSet"}),
     ("stig", "rmf", "compliance", "nist", "800-53", "hardening", "cci")),
    (frozenset({"GraphQuery", "GraphAdd", "Knowledge", "BuildKnowledge"}),
     ("knowledge", "graph", "entity", "graphrag", "ingest")),
    (frozenset({"ViewImage", "Screenshot"}),
     ("image", "screenshot", "photo", "picture", "diagram", "png", "jpg", "visual")),
]

# External/mutating tools deprioritised while merely exploring (PRD F1.4: read &
# search preferred in DISCOVER, destructive external tools not surfaced).
_EXTERNAL_MUTATION = frozenset({
    "GitCommit", "GraphAdd", "BuildKnowledge", "StigSet", "Worker",
})

_DISCOVERY_PHASES = frozenset({"understand", "discover", "plan"})


def _priority(name: str, phase: str, task: str) -> int:
    """Higher = keep. CORE tools sort above everything; the rest rank by phase
    preference and task relevance so the cap drops the least-relevant first."""
    if name in CORE_TOOLS:
        return 1000
    score = 50  # neutral default — unknown/uncategorised tools are still kept if room
    phase = (phase or "").lower()
    task = (task or "").lower()

    # task relevance: a family whose keyword appears is strongly boosted; one whose
    # keyword is absent is pushed down so it's the first to go when over the cap.
    for members, keywords in _FAMILIES:
        if name in members:
            score += 40 if any(k in task for k in keywords) else -30
            break

    if phase in _DISCOVERY_PHASES:
        if name in READ_SEARCH_TOOLS:
            score += 20
        if name in _EXTERNAL_MUTATION:
            score -= 40  # don't surface external mutations while exploring
    return score


def select_tools(
    schemas: list[dict],
    *,
    phase: str = "",
    task_text: str = "",
    max_tools: int = DEFAULT_MAX_TOOLS,
) -> list[dict]:
    """Return the subset of `schemas` to expose this turn (PRD Epic F).

    Keeps every core coding tool, prefers read/search while exploring, and
    surfaces specialised families only when the task text mentions them —
    trimming to `max_tools` only when the full set exceeds it. Order-preserving
    for the kept tools so the model sees a stable list. Never raises."""
    if not schemas or max_tools <= 0 or len(schemas) <= max_tools:
        return schemas

    ranked = sorted(
        schemas,
        key=lambda s: _priority(s.get("name", ""), phase, task_text),
        reverse=True,
    )
    keep_names = {s.get("name") for s in ranked[:max_tools]}
    # Preserve the original ordering among the kept tools.
    return [s for s in schemas if s.get("name") in keep_names]
