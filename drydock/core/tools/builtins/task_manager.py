"""Task lifecycle tools — create, get, list, update tasks.

Provides interactive task management for tracking work items
during a session, similar to Claude Code's TaskCreate/Get/List/Update.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import ClassVar, final
from uuid import uuid4

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent, ToolResultEvent

# In-memory task store (per session)
_TASKS: dict[str, dict] = {}
_TASK_COUNTER = 0


class TaskCreateArgs(BaseModel):
    title: str = Field(description="Brief title for the task")
    description: str = Field(default="", description="Detailed description")


class TaskCreateResult(BaseModel):
    task_id: str
    title: str
    status: str = "pending"


class TaskCreate(
    BaseTool[TaskCreateArgs, TaskCreateResult, BaseToolConfig, BaseToolState],
    ToolUIData[TaskCreateArgs, TaskCreateResult],
):
    description: ClassVar[str] = "Create a new task to track work."

    @classmethod
    def format_call_display(cls, args: TaskCreateArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"task_create: {args.title[:50]}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, TaskCreateResult):
            return ToolResultDisplay(success=True, message=f"Created task #{event.result.task_id}")
        return ToolResultDisplay(success=True, message="Task created")

    @classmethod
    def get_status_text(cls) -> str:
        return "Creating task"

    def resolve_permission(self, args: TaskCreateArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: TaskCreateArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | TaskCreateResult, None]:
        global _TASK_COUNTER
        _TASK_COUNTER += 1
        task_id = str(_TASK_COUNTER)

        _TASKS[task_id] = {
            "id": task_id,
            "title": args.title,
            "description": args.description,
            "status": "pending",
        }

        yield TaskCreateResult(task_id=task_id, title=args.title)


class TaskListArgs(BaseModel):
    pass


class TaskListResult(BaseModel):
    tasks: list[dict]
    count: int


class TaskList(
    BaseTool[TaskListArgs, TaskListResult, BaseToolConfig, BaseToolState],
    ToolUIData[TaskListArgs, TaskListResult],
):
    description: ClassVar[str] = "List all tasks and their status."

    @classmethod
    def format_call_display(cls, args: TaskListArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="task_list")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, TaskListResult):
            return ToolResultDisplay(success=True, message=f"{event.result.count} tasks")
        return ToolResultDisplay(success=True, message="Tasks listed")

    @classmethod
    def get_status_text(cls) -> str:
        return "Listing tasks"

    def resolve_permission(self, args: TaskListArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: TaskListArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | TaskListResult, None]:
        tasks = list(_TASKS.values())
        yield TaskListResult(tasks=tasks, count=len(tasks))


class TaskUpdateArgs(BaseModel):
    task_id: str = Field(description="Task ID to update")
    status: str = Field(description="New status: pending, in_progress, completed, blocked")
    notes: str = Field(default="", description="Optional notes")


class TaskUpdateResult(BaseModel):
    task_id: str
    status: str
    title: str


class TaskUpdate(
    BaseTool[TaskUpdateArgs, TaskUpdateResult, BaseToolConfig, BaseToolState],
    ToolUIData[TaskUpdateArgs, TaskUpdateResult],
):
    description: ClassVar[str] = "Update a task's status."

    @classmethod
    def format_call_display(cls, args: TaskUpdateArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"task_update: #{args.task_id} → {args.status}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, TaskUpdateResult):
            return ToolResultDisplay(success=True, message=f"#{event.result.task_id} → {event.result.status}")
        return ToolResultDisplay(success=True, message="Task updated")

    @classmethod
    def get_status_text(cls) -> str:
        return "Updating task"

    def resolve_permission(self, args: TaskUpdateArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: TaskUpdateArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | TaskUpdateResult, None]:
        if args.task_id not in _TASKS:
            raise ToolError(f"Task #{args.task_id} not found")

        task = _TASKS[args.task_id]
        task["status"] = args.status
        if args.notes:
            task["notes"] = args.notes

        yield TaskUpdateResult(
            task_id=args.task_id, status=args.status, title=task["title"]
        )
