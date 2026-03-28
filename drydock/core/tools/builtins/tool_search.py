"""ToolSearch — deferred tool loading for MCP scale.

When many MCP servers are connected, not all tools need to be loaded
at once. ToolSearch lets the model search for tools by keyword and
load them on demand.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent, ToolResultEvent


class ToolSearchArgs(BaseModel):
    query: str = Field(description="Search query (keyword or tool name)")
    max_results: int = Field(default=10, description="Maximum results to return")


class ToolInfo(BaseModel):
    name: str
    description: str
    source: str = "builtin"  # "builtin", "mcp", "custom"


class ToolSearchResult(BaseModel):
    tools: list[ToolInfo]
    count: int


class ToolSearch(
    BaseTool[ToolSearchArgs, ToolSearchResult, BaseToolConfig, BaseToolState],
    ToolUIData[ToolSearchArgs, ToolSearchResult],
):
    description: ClassVar[str] = (
        "Search for available tools by name or keyword. "
        "Use this to discover tools from MCP servers."
    )

    @classmethod
    def format_call_display(cls, args: ToolSearchArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"tool_search: {args.query}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, ToolSearchResult):
            return ToolResultDisplay(success=True, message=f"Found {event.result.count} tools")
        return ToolResultDisplay(success=True, message="Search complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Searching tools"

    def resolve_permission(self, args: ToolSearchArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: ToolSearchArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ToolSearchResult, None]:
        results: list[ToolInfo] = []
        query_lower = args.query.lower()

        if ctx and ctx.agent_manager:
            try:
                from drydock.core.tools.manager import ToolManager
                config = ctx.agent_manager.config
                manager = ToolManager(lambda: config)

                for tool_name in manager.all_tool_names():
                    tool = manager.get(tool_name)
                    desc = getattr(tool, 'description', '') or ''
                    if query_lower in tool_name.lower() or query_lower in desc.lower():
                        source = "mcp" if "_" in tool_name and not hasattr(tool, 'FRAMES') else "builtin"
                        results.append(ToolInfo(name=tool_name, description=desc[:200], source=source))
            except Exception:
                pass

        # Also search builtin tool names directly
        builtin_tools = {
            "bash": "Run shell commands",
            "grep": "Search file contents with regex",
            "glob": "Find files by pattern",
            "read_file": "Read file contents",
            "write_file": "Create or overwrite a file",
            "search_replace": "Edit files with search/replace blocks",
            "webfetch": "Fetch a URL and return content",
            "websearch": "Search the web via DuckDuckGo",
            "todo": "Manage a todo list",
            "task": "Delegate work to a subagent",
            "task_create": "Create a task to track work",
            "task_list": "List all tasks",
            "task_update": "Update task status",
            "ask_user_question": "Ask the user a question",
            "invoke_skill": "Invoke a skill by name",
            "enter_worktree": "Create git worktree",
            "exit_worktree": "Exit git worktree",
            "notebook_edit": "Edit Jupyter notebook cells",
            "cron_create": "Schedule recurring prompt",
            "cron_list": "List scheduled crons",
            "cron_delete": "Delete a cron",
            "list_mcp_resources": "List MCP resources",
            "read_mcp_resource": "Read MCP resource",
            "tool_search": "Search for tools (this tool)",
        }

        for name, desc in builtin_tools.items():
            if query_lower in name or query_lower in desc.lower():
                if not any(r.name == name for r in results):
                    results.append(ToolInfo(name=name, description=desc, source="builtin"))

        results = results[:args.max_results]
        yield ToolSearchResult(tools=results, count=len(results))
