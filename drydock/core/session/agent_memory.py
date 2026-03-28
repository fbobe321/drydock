"""Subagent persistent memory — cross-session learning.

Each subagent can store and recall memories that persist across sessions.
Memories are stored as markdown files in ~/.drydock/agent_memory/<agent_name>/.

Types:
- user: Info about the user's preferences and context
- project: Project-specific decisions and state
- feedback: Corrections and confirmed approaches
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_memory_dir(agent_name: str) -> Path:
    """Get the memory directory for a specific agent."""
    try:
        from drydock.core.paths import VIBE_HOME
        base = VIBE_HOME.path / "agent_memory" / agent_name
    except Exception:
        base = Path.home() / ".drydock" / "agent_memory" / agent_name
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_memory(
    agent_name: str,
    key: str,
    content: str,
    memory_type: str = "project",
) -> None:
    """Save a memory for an agent."""
    mem_dir = _get_memory_dir(agent_name)
    mem_file = mem_dir / f"{key}.md"

    text = f"""---
key: {key}
type: {memory_type}
updated: {datetime.now().isoformat()}
agent: {agent_name}
---

{content}
"""
    try:
        mem_file.write_text(text, encoding="utf-8")
        logger.info("Saved memory '%s' for agent '%s'", key, agent_name)
    except (OSError, PermissionError) as e:
        logger.warning("Could not save memory: %s", e)


def load_memories(agent_name: str, memory_type: str = "") -> dict[str, str]:
    """Load all memories for an agent, optionally filtered by type."""
    mem_dir = _get_memory_dir(agent_name)
    memories: dict[str, str] = {}

    try:
        for f in sorted(mem_dir.glob("*.md")):
            content = f.read_text(encoding="utf-8")
            # Check type filter
            if memory_type and f"type: {memory_type}" not in content:
                continue
            # Strip frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                body = parts[2].strip() if len(parts) >= 3 else content
            else:
                body = content
            memories[f.stem] = body
    except (OSError, PermissionError):
        pass

    return memories


def load_all_memories_as_context(agent_name: str, max_chars: int = 2000) -> str:
    """Load all memories as a context string for injection into prompts."""
    memories = load_memories(agent_name)
    if not memories:
        return ""

    lines = [f"## Agent Memory ({agent_name})"]
    chars = 0
    for key, content in memories.items():
        entry = f"\n### {key}\n{content[:300]}"
        if chars + len(entry) > max_chars:
            break
        lines.append(entry)
        chars += len(entry)

    return "\n".join(lines)


def delete_memory(agent_name: str, key: str) -> bool:
    """Delete a specific memory."""
    mem_dir = _get_memory_dir(agent_name)
    mem_file = mem_dir / f"{key}.md"
    try:
        if mem_file.exists():
            mem_file.unlink()
            return True
    except (OSError, PermissionError):
        pass
    return False


def clear_agent_memories(agent_name: str) -> int:
    """Clear all memories for an agent. Returns count of deleted."""
    mem_dir = _get_memory_dir(agent_name)
    count = 0
    try:
        for f in mem_dir.glob("*.md"):
            f.unlink()
            count += 1
    except (OSError, PermissionError):
        pass
    return count
