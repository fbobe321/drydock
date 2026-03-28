"""Glob tool — fast file pattern matching.

Matches files by glob pattern (e.g., "**/*.py", "src/**/*.ts").
Returns sorted file paths. Read-only, always auto-approved.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from drydock.core.types import ToolResultEvent


class GlobArgs(BaseModel):
    pattern: str = Field(description="Glob pattern (e.g., '**/*.py', 'src/**/*.ts')")
    path: str = Field(default=".", description="Directory to search in")


class GlobResult(BaseModel):
    files: list[str]
    count: int
    truncated: bool = False


class GlobConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    max_results: int = 200


class Glob(
    BaseTool[GlobArgs, GlobResult, GlobConfig, BaseToolState],
    ToolUIData[GlobArgs, GlobResult],
):
    description: ClassVar[str] = (
        "Find files matching a glob pattern. Fast file discovery."
    )

    @classmethod
    def format_call_display(cls, args: GlobArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"glob: {args.pattern}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, GlobResult):
            return ToolResultDisplay(
                success=True, message=f"Found {event.result.count} files"
            )
        return ToolResultDisplay(success=True, message="Glob complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Searching files"

    def resolve_permission(self, args: GlobArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: GlobArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GlobResult, None]:
        search_dir = Path(args.path).expanduser()
        if not search_dir.is_absolute():
            search_dir = Path.cwd() / search_dir

        if not search_dir.is_dir():
            raise ToolError(f"Directory not found: {search_dir}")

        try:
            matches = sorted(
                str(p.relative_to(search_dir))
                for p in search_dir.glob(args.pattern)
                if p.is_file()
                and "__pycache__" not in str(p)
                and ".git/" not in str(p)
                and "node_modules/" not in str(p)
            )
        except Exception as e:
            raise ToolError(f"Glob error: {e}") from e

        truncated = len(matches) > self.config.max_results
        files = matches[:self.config.max_results]

        yield GlobResult(files=files, count=len(matches), truncated=truncated)
