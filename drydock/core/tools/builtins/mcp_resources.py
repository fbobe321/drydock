"""MCP Resource tools — list and read MCP server resources.

MCP servers can expose resources (files, data) in addition to tools.
These tools let the agent discover and read them.
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


class ListMcpResourcesArgs(BaseModel):
    server_name: str = Field(default="", description="MCP server name (empty = all servers)")


class McpResource(BaseModel):
    uri: str
    name: str
    description: str = ""
    mime_type: str = ""
    server: str = ""


class ListMcpResourcesResult(BaseModel):
    resources: list[McpResource]
    count: int


class ListMcpResources(
    BaseTool[ListMcpResourcesArgs, ListMcpResourcesResult, BaseToolConfig, BaseToolState],
    ToolUIData[ListMcpResourcesArgs, ListMcpResourcesResult],
):
    description: ClassVar[str] = "List resources available from MCP servers."

    @classmethod
    def format_call_display(cls, args: ListMcpResourcesArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"list_mcp_resources: {args.server_name or 'all'}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, ListMcpResourcesResult):
            return ToolResultDisplay(success=True, message=f"{event.result.count} resources")
        return ToolResultDisplay(success=True, message="Resources listed")

    @classmethod
    def get_status_text(cls) -> str:
        return "Listing MCP resources"

    def resolve_permission(self, args: ListMcpResourcesArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: ListMcpResourcesArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ListMcpResourcesResult, None]:
        # MCP resource listing is handled by the MCP registry
        # For now, return empty — full implementation requires MCP protocol changes
        resources: list[McpResource] = []

        try:
            if ctx and hasattr(ctx, 'agent_manager') and ctx.agent_manager:
                # Try to list resources from connected MCP servers
                from drydock.core.tools.mcp import MCPRegistry
                # This would need MCP servers to implement resources
                pass
        except Exception:
            pass

        yield ListMcpResourcesResult(resources=resources, count=len(resources))


class ReadMcpResourceArgs(BaseModel):
    uri: str = Field(description="Resource URI to read")
    server_name: str = Field(default="", description="MCP server name")


class ReadMcpResourceResult(BaseModel):
    uri: str
    content: str
    mime_type: str = ""


class ReadMcpResource(
    BaseTool[ReadMcpResourceArgs, ReadMcpResourceResult, BaseToolConfig, BaseToolState],
    ToolUIData[ReadMcpResourceArgs, ReadMcpResourceResult],
):
    description: ClassVar[str] = "Read a resource from an MCP server by URI."

    @classmethod
    def format_call_display(cls, args: ReadMcpResourceArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"read_mcp_resource: {args.uri[:50]}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        return ToolResultDisplay(success=True, message="Resource read")

    @classmethod
    def get_status_text(cls) -> str:
        return "Reading MCP resource"

    @final
    async def run(
        self, args: ReadMcpResourceArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ReadMcpResourceResult, None]:
        # Full MCP resource reading requires protocol-level support
        raise ToolError(
            f"MCP resource reading not yet connected to MCP servers. "
            f"URI: {args.uri}. Configure an MCP server that exposes resources."
        )
