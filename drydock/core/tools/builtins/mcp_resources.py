"""MCP Resource tools — list and read MCP server resources.

MCP servers can expose resources (files, data) in addition to tools.
These tools let the agent discover and read them.
"""

from __future__ import annotations

import asyncio
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
        resources: list[McpResource] = []

        try:
            if ctx and hasattr(ctx, 'agent_manager') and ctx.agent_manager:
                config = ctx.agent_manager.config
                servers = config.mcp_servers or []

                from drydock.core.tools.mcp.tools import (
                    list_resources_http, list_resources_stdio,
                )
                from drydock.core.config import MCPStdio, MCPHttp

                tasks = []
                for srv in servers:
                    if args.server_name and getattr(srv, 'alias', '') != args.server_name:
                        continue
                    if isinstance(srv, MCPHttp):
                        tasks.append(list_resources_http(
                            srv.url,
                            headers=srv.headers,
                            startup_timeout_sec=srv.startup_timeout_seconds,
                        ))
                    elif isinstance(srv, MCPStdio):
                        tasks.append(list_resources_stdio(
                            srv.command,
                            env=srv.env,
                            startup_timeout_sec=srv.startup_timeout_seconds,
                        ))

                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for result in results:
                        if isinstance(result, list):
                            for r in result:
                                resources.append(McpResource(
                                    uri=r.uri, name=r.name,
                                    description=r.description,
                                    mime_type=r.mime_type,
                                    server=r.server,
                                ))
        except Exception as e:
            raise ToolError(f"Failed to list MCP resources: {e}") from e

        yield ListMcpResourcesResult(resources=resources, count=len(resources))


class ReadMcpResourceArgs(BaseModel):
    uri: str = Field(description="Resource URI to read")
    server_name: str = Field(default="", description="MCP server name (required if multiple servers)")


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

    def resolve_permission(self, args: ReadMcpResourceArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: ReadMcpResourceArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ReadMcpResourceResult, None]:
        if not ctx or not hasattr(ctx, 'agent_manager') or not ctx.agent_manager:
            raise ToolError("No agent manager available for MCP resource reading")

        config = ctx.agent_manager.config
        servers = config.mcp_servers or []

        if not servers:
            raise ToolError("No MCP servers configured. Add servers to ~/.drydock/config.toml")

        from drydock.core.tools.mcp.tools import read_resource_http, read_resource_stdio
        from drydock.core.config import MCPStdio, MCPHttp

        # Try each server until we find the resource
        last_error = None
        for srv in servers:
            if args.server_name and getattr(srv, 'alias', '') != args.server_name:
                continue
            try:
                if isinstance(srv, MCPHttp):
                    content = await read_resource_http(
                        srv.url, args.uri,
                        headers=srv.headers,
                        startup_timeout_sec=srv.startup_timeout_seconds,
                    )
                elif isinstance(srv, MCPStdio):
                    content = await read_resource_stdio(
                        srv.command, args.uri,
                        env=srv.env,
                        startup_timeout_sec=srv.startup_timeout_seconds,
                    )
                else:
                    continue

                yield ReadMcpResourceResult(
                    uri=args.uri,
                    content=content,
                    mime_type="",
                )
                return
            except Exception as e:
                last_error = e
                continue

        raise ToolError(
            f"Could not read resource '{args.uri}' from any MCP server. "
            f"Last error: {last_error}"
        )
