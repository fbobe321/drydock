"""Worktree tools — git worktree isolation for parallel work.

EnterWorktree creates an isolated copy of the repo.
ExitWorktree returns to the main working directory.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import ClassVar, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent, ToolResultEvent


class EnterWorktreeArgs(BaseModel):
    branch: str = Field(default="", description="Branch name for the worktree (auto-generated if empty)")


class EnterWorktreeResult(BaseModel):
    worktree_path: str
    branch: str
    original_dir: str


class EnterWorktree(
    BaseTool[EnterWorktreeArgs, EnterWorktreeResult, BaseToolConfig, BaseToolState],
    ToolUIData[EnterWorktreeArgs, EnterWorktreeResult],
):
    description: ClassVar[str] = "Create an isolated git worktree for parallel work."

    @classmethod
    def format_call_display(cls, args: EnterWorktreeArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"enter_worktree: {args.branch or 'auto'}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, EnterWorktreeResult):
            return ToolResultDisplay(success=True, message=f"Worktree: {event.result.branch}")
        return ToolResultDisplay(success=True, message="Worktree created")

    @classmethod
    def get_status_text(cls) -> str:
        return "Creating worktree"

    @final
    async def run(
        self, args: EnterWorktreeArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | EnterWorktreeResult, None]:
        cwd = Path.cwd()

        # Check we're in a git repo
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--git-dir",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise ToolError("Not in a git repository")

        branch = args.branch or f"drydock-worktree-{os.getpid()}"
        worktree_path = cwd.parent / f".drydock-worktree-{branch}"

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", "-b", branch, str(worktree_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                # Try without -b (branch might exist)
                proc = await asyncio.create_subprocess_exec(
                    "git", "worktree", "add", str(worktree_path), branch,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise ToolError(f"Failed to create worktree: {stderr.decode()}")

            os.chdir(worktree_path)

            yield EnterWorktreeResult(
                worktree_path=str(worktree_path),
                branch=branch,
                original_dir=str(cwd),
            )
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Worktree error: {e}") from e


class ExitWorktreeArgs(BaseModel):
    cleanup: bool = Field(default=False, description="Remove the worktree after exiting")


class ExitWorktreeResult(BaseModel):
    returned_to: str
    cleaned_up: bool


class ExitWorktree(
    BaseTool[ExitWorktreeArgs, ExitWorktreeResult, BaseToolConfig, BaseToolState],
    ToolUIData[ExitWorktreeArgs, ExitWorktreeResult],
):
    description: ClassVar[str] = "Exit the current git worktree and return to main directory."

    @classmethod
    def format_call_display(cls, args: ExitWorktreeArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="exit_worktree")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        return ToolResultDisplay(success=True, message="Returned to main")

    @classmethod
    def get_status_text(cls) -> str:
        return "Exiting worktree"

    @final
    async def run(
        self, args: ExitWorktreeArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ExitWorktreeResult, None]:
        cwd = Path.cwd()

        # Find the main worktree
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "list", "--porcelain",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        # First worktree in the list is the main one
        main_dir = ""
        for line in stdout.decode().split("\n"):
            if line.startswith("worktree "):
                main_dir = line.split(" ", 1)[1].strip()
                break

        if not main_dir or not Path(main_dir).is_dir():
            raise ToolError("Cannot find main worktree directory")

        os.chdir(main_dir)

        cleaned = False
        if args.cleanup and str(cwd) != main_dir:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "worktree", "remove", "--force", str(cwd),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                cleaned = proc.returncode == 0
            except Exception:
                pass

        yield ExitWorktreeResult(returned_to=main_dir, cleaned_up=cleaned)
