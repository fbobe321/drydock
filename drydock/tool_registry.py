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
    """Execute a tool by name. Truncates large output. Returns the text string
    (backward-compatible); use execute_structured() for the ToolResult."""
    return execute_structured(name, params, config, max_output).text


def execute_structured(name: str, params: dict, config: dict, max_output: int = 32000):
    """Execute a tool and return a structured ToolResult (status/diagnostics/
    retryable/duration + the text), classified from the tool's string output +
    timing. The individual tools still return plain strings — this WRAPS them."""
    import time as _time

    from drydock.tool_result import ToolResult, classify

    tool = get(name)
    if tool is None:
        return ToolResult(name=name, status="not_found", error_code="not_found",
                          text=f"Error: tool '{name}' not found.")
    start = _time.monotonic()
    try:
        result = tool.func(params, config)
    except Exception as e:
        dur = int((_time.monotonic() - start) * 1000)
        return ToolResult(name=name, status="error", error_code="exception",
                          text=f"Error executing {name}: {e}", duration_ms=dur)
    dur = int((_time.monotonic() - start) * 1000)

    if len(result) > max_output:
        half = max_output // 2
        quarter = max_output // 4
        result = result[:half] + f"\n[... {len(result) - half - quarter} chars truncated ...]\n" + result[-quarter:]
    return classify(name, params, result, dur)
