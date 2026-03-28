"""Cron tools — schedule recurring or one-shot prompts.

CronCreate: Schedule a prompt to run on an interval
CronList: List active scheduled prompts
CronDelete: Remove a scheduled prompt
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import ClassVar, final
from uuid import uuid4

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent, ToolResultEvent

# In-memory cron store (per session)
_CRONS: dict[str, dict] = {}
_CRON_TASKS: dict[str, asyncio.Task] = {}


class CronCreateArgs(BaseModel):
    prompt: str = Field(description="The prompt to run on schedule")
    interval_minutes: int = Field(default=10, description="Interval in minutes between runs")
    name: str = Field(default="", description="Optional name for the cron job")
    max_runs: int = Field(default=0, description="Max runs (0 = unlimited)")


class CronCreateResult(BaseModel):
    cron_id: str
    name: str
    interval_minutes: int
    prompt: str


class CronCreate(
    BaseTool[CronCreateArgs, CronCreateResult, BaseToolConfig, BaseToolState],
    ToolUIData[CronCreateArgs, CronCreateResult],
):
    description: ClassVar[str] = "Schedule a prompt to run on a recurring interval."

    @classmethod
    def format_call_display(cls, args: CronCreateArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"cron_create: every {args.interval_minutes}m")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, CronCreateResult):
            return ToolResultDisplay(success=True, message=f"Cron '{event.result.name}' created")
        return ToolResultDisplay(success=True, message="Cron created")

    @classmethod
    def get_status_text(cls) -> str:
        return "Creating cron"

    @final
    async def run(
        self, args: CronCreateArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | CronCreateResult, None]:
        cron_id = str(uuid4())[:8]
        name = args.name or f"cron-{cron_id}"

        _CRONS[cron_id] = {
            "id": cron_id,
            "name": name,
            "prompt": args.prompt,
            "interval_minutes": args.interval_minutes,
            "max_runs": args.max_runs,
            "runs": 0,
            "status": "active",
        }

        yield CronCreateResult(
            cron_id=cron_id, name=name,
            interval_minutes=args.interval_minutes, prompt=args.prompt,
        )


class CronListArgs(BaseModel):
    pass


class CronListResult(BaseModel):
    crons: list[dict]
    count: int


class CronList(
    BaseTool[CronListArgs, CronListResult, BaseToolConfig, BaseToolState],
    ToolUIData[CronListArgs, CronListResult],
):
    description: ClassVar[str] = "List all scheduled cron jobs."

    @classmethod
    def format_call_display(cls, args: CronListArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="cron_list")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, CronListResult):
            return ToolResultDisplay(success=True, message=f"{event.result.count} crons")
        return ToolResultDisplay(success=True, message="Crons listed")

    @classmethod
    def get_status_text(cls) -> str:
        return "Listing crons"

    def resolve_permission(self, args: CronListArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: CronListArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | CronListResult, None]:
        yield CronListResult(crons=list(_CRONS.values()), count=len(_CRONS))


class CronDeleteArgs(BaseModel):
    cron_id: str = Field(description="ID of the cron job to delete")


class CronDeleteResult(BaseModel):
    cron_id: str
    deleted: bool


class CronDelete(
    BaseTool[CronDeleteArgs, CronDeleteResult, BaseToolConfig, BaseToolState],
    ToolUIData[CronDeleteArgs, CronDeleteResult],
):
    description: ClassVar[str] = "Delete a scheduled cron job."

    @classmethod
    def format_call_display(cls, args: CronDeleteArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"cron_delete: {args.cron_id}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        return ToolResultDisplay(success=True, message="Cron deleted")

    @classmethod
    def get_status_text(cls) -> str:
        return "Deleting cron"

    @final
    async def run(
        self, args: CronDeleteArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | CronDeleteResult, None]:
        if args.cron_id not in _CRONS:
            raise ToolError(f"Cron '{args.cron_id}' not found")

        # Cancel async task if running
        if args.cron_id in _CRON_TASKS:
            _CRON_TASKS[args.cron_id].cancel()
            del _CRON_TASKS[args.cron_id]

        del _CRONS[args.cron_id]
        yield CronDeleteResult(cron_id=args.cron_id, deleted=True)
