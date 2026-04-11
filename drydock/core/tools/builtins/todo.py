from __future__ import annotations

from collections.abc import AsyncGenerator
from enum import StrEnum, auto
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolResultEvent, ToolStreamEvent


class TodoStatus(StrEnum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    CANCELLED = auto()


class TodoPriority(StrEnum):
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()


class TodoItem(BaseModel):
    id: str = Field(default_factory=lambda: str(__import__('time').time_ns()))
    content: str = ""
    status: TodoStatus = TodoStatus.PENDING
    priority: TodoPriority = TodoPriority.MEDIUM

    @field_validator("status", mode="before")
    @classmethod
    def clean_status(cls, v):
        if isinstance(v, str):
            return v.strip("'\"").lower()
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def clean_priority(cls, v):
        if isinstance(v, str):
            return v.strip("'\"").lower()
        return v


class TodoArgs(BaseModel):
    action: str = Field(default="read", description="Either 'read' or 'write'")
    todos: list[TodoItem] | None = Field(
        default=None, description="Complete list of todos when writing."
    )

    @field_validator("todos", mode="before")
    @classmethod
    def coerce_todos(cls, v: Any) -> Any:
        """Gemma 4 sends todos in various malformed formats.

        Common patterns:
          - A single string: "1. Do this 2. Do that"
          - A list of strings: ["Do this", "Do that"]
          - A list of dicts missing required fields

        Coerce all of these into list[TodoItem]-compatible dicts.
        """
        if v is None:
            return v
        if isinstance(v, str):
            # Split numbered or newline-separated items
            import re
            items = re.split(r'\n|(?:\d+\.\s)', v)
            return [
                {"content": item.strip(), "status": "pending"}
                for item in items if item.strip()
            ]
        if isinstance(v, list):
            result = []
            for item in v:
                if isinstance(item, str):
                    result.append({"content": item.strip(), "status": "pending"})
                elif isinstance(item, dict):
                    result.append(item)
                else:
                    result.append({"content": str(item), "status": "pending"})
            return result
        return v


class TodoResult(BaseModel):
    message: str
    todos: list[TodoItem]
    total_count: int


class TodoConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    max_todos: int = 100


class TodoState(BaseToolState):
    todos: list[TodoItem] = Field(default_factory=list)


class Todo(
    BaseTool[TodoArgs, TodoResult, TodoConfig, TodoState],
    ToolUIData[TodoArgs, TodoResult],
):
    description: ClassVar[str] = (
        "Manage todos. Use action='read' to view, action='write' with complete list to update."
    )

    @classmethod
    def format_call_display(cls, args: TodoArgs) -> ToolCallDisplay:
        match args.action:
            case "read":
                return ToolCallDisplay(summary="Reading todos")
            case "write":
                count = len(args.todos) if args.todos else 0
                return ToolCallDisplay(summary=f"Writing {count} todos")
            case _:
                return ToolCallDisplay(summary=f"Unknown action: {args.action}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, TodoResult):
            return ToolResultDisplay(success=True, message="Success")

        result = event.result

        return ToolResultDisplay(success=True, message=result.message)

    @classmethod
    def get_status_text(cls) -> str:
        return "Managing todos"

    async def run(
        self, args: TodoArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | TodoResult, None]:
        match args.action:
            case "read":
                yield self._read_todos()
            case "write":
                yield self._write_todos(args.todos or [])
            case _:
                raise ToolError(
                    f"Invalid action '{args.action}'. Use 'read' or 'write'."
                )

    def _read_todos(self) -> TodoResult:
        return TodoResult(
            message=f"Retrieved {len(self.state.todos)} todos",
            todos=self.state.todos,
            total_count=len(self.state.todos),
        )

    def _write_todos(self, todos: list[TodoItem]) -> TodoResult:
        if len(todos) > self.config.max_todos:
            raise ToolError(f"Cannot store more than {self.config.max_todos} todos")

        ids = [todo.id for todo in todos]
        if len(ids) != len(set(ids)):
            raise ToolError("Todo IDs must be unique")

        self.state.todos = todos

        return TodoResult(
            message=f"Updated {len(todos)} todos",
            todos=self.state.todos,
            total_count=len(self.state.todos),
        )
