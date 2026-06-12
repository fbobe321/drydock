"""Tool plugin registry — register, lookup, execute tools.

DryDock v3 — simple dataclass-based tool system.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

@dataclass
class ToolDef:
    """A single tool definition."""
    name: str
    schema: dict  # JSON schema sent to LLM API
    func: Callable[[dict, dict], str]  # (params, config) -> result string
    read_only: bool = False

_registry: dict[str, ToolDef] = {}

def register(tool: ToolDef) -> None:
    _registry[tool.name] = tool

def get(name: str) -> ToolDef | None:
    return _registry.get(name)

def all_tools() -> list[ToolDef]:
    return list(_registry.values())

def schemas() -> list[dict]:
    return [t.schema for t in _registry.values()]

def execute(name: str, params: dict, config: dict, max_output: int = 32000) -> str:
    """Execute a tool by name. Truncates large output."""
    tool = get(name)
    if tool is None:
        return f"Error: tool '{name}' not found."
    try:
        result = tool.func(params, config)
    except Exception as e:
        return f"Error executing {name}: {e}"

    if len(result) > max_output:
        half = max_output // 2
        quarter = max_output // 4
        result = result[:half] + f"\n[... {len(result) - half - quarter} chars truncated ...]\n" + result[-quarter:]
    return result
