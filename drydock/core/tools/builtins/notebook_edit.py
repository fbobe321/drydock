"""NotebookEdit tool — edit Jupyter notebook cells.

Supports editing code and markdown cells in .ipynb files.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import ClassVar, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent, ToolResultEvent


class NotebookEditArgs(BaseModel):
    path: str = Field(description="Path to the .ipynb file")
    cell_index: int = Field(description="Cell index to edit (0-based)")
    new_source: str = Field(description="New source content for the cell")
    cell_type: str = Field(default="code", description="Cell type: 'code' or 'markdown'")


class NotebookEditResult(BaseModel):
    path: str
    cell_index: int
    cell_type: str
    total_cells: int


class NotebookEdit(
    BaseTool[NotebookEditArgs, NotebookEditResult, BaseToolConfig, BaseToolState],
    ToolUIData[NotebookEditArgs, NotebookEditResult],
):
    description: ClassVar[str] = "Edit a cell in a Jupyter notebook (.ipynb file)."

    @classmethod
    def format_call_display(cls, args: NotebookEditArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"notebook_edit: cell {args.cell_index} in {args.path}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, NotebookEditResult):
            return ToolResultDisplay(success=True, message=f"Edited cell {event.result.cell_index}")
        return ToolResultDisplay(success=True, message="Notebook edited")

    @classmethod
    def get_status_text(cls) -> str:
        return "Editing notebook"

    @final
    async def run(
        self, args: NotebookEditArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | NotebookEditResult, None]:
        file_path = Path(args.path).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path

        if not file_path.exists():
            raise ToolError(f"Notebook not found: {file_path}")

        if not str(file_path).endswith(".ipynb"):
            raise ToolError("File must be a .ipynb notebook")

        try:
            with open(file_path, encoding="utf-8") as f:
                notebook = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise ToolError(f"Cannot read notebook: {e}") from e

        cells = notebook.get("cells", [])
        if args.cell_index < 0 or args.cell_index >= len(cells):
            raise ToolError(f"Cell index {args.cell_index} out of range (0-{len(cells)-1})")

        # Update the cell
        cell = cells[args.cell_index]
        cell["source"] = args.new_source.split("\n")
        cell["cell_type"] = args.cell_type

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(notebook, f, indent=1, ensure_ascii=False)
        except OSError as e:
            raise ToolError(f"Cannot write notebook: {e}") from e

        yield NotebookEditResult(
            path=str(file_path),
            cell_index=args.cell_index,
            cell_type=args.cell_type,
            total_cells=len(cells),
        )
