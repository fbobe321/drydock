"""PowerShell tool — native Windows PowerShell execution.

Similar to bash but uses PowerShell for Windows environments.
On non-Windows systems, falls back to pwsh if available.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncGenerator
from typing import ClassVar, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent, ToolResultEvent


class PowerShellArgs(BaseModel):
    command: str = Field(description="PowerShell command to execute")
    timeout: int | None = Field(default=None, description="Override default timeout")


class PowerShellResult(BaseModel):
    command: str
    stdout: str
    stderr: str
    returncode: int


class PowerShellConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    default_timeout: int = 300
    max_output_bytes: int = 16_000


class PowerShell(
    BaseTool[PowerShellArgs, PowerShellResult, PowerShellConfig, BaseToolState],
    ToolUIData[PowerShellArgs, PowerShellResult],
):
    description: ClassVar[str] = "Run a PowerShell command (Windows or pwsh on Linux/macOS)."

    @classmethod
    def is_available(cls) -> bool:
        """Check if PowerShell is available."""
        import shutil
        if sys.platform == "win32":
            return True
        return shutil.which("pwsh") is not None

    @classmethod
    def format_call_display(cls, args: PowerShellArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"powershell: {args.command[:60]}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, PowerShellResult):
            return ToolResultDisplay(success=True, message=f"Ran {event.result.command[:40]}")
        return ToolResultDisplay(success=True, message="PowerShell complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Running PowerShell"

    @final
    async def run(
        self, args: PowerShellArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | PowerShellResult, None]:
        timeout = args.timeout or self.config.default_timeout

        # Determine PowerShell executable
        if sys.platform == "win32":
            ps_cmd = ["powershell", "-NoProfile", "-Command", args.command]
        else:
            import shutil
            pwsh = shutil.which("pwsh")
            if not pwsh:
                raise ToolError("PowerShell (pwsh) not found. Install: https://aka.ms/install-powershell")
            ps_cmd = [pwsh, "-NoProfile", "-Command", args.command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *ps_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            stdout_str = stdout.decode("utf-8", errors="replace")[:self.config.max_output_bytes]
            stderr_str = stderr.decode("utf-8", errors="replace")[:self.config.max_output_bytes]

            if proc.returncode != 0:
                raise ToolError(
                    f"PowerShell failed: {args.command}\n"
                    f"Return code: {proc.returncode}\n"
                    f"Stderr: {stderr_str}\nStdout: {stdout_str}"
                )

            yield PowerShellResult(
                command=args.command,
                stdout=stdout_str,
                stderr=stderr_str,
                returncode=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            raise ToolError(f"PowerShell timed out after {timeout}s: {args.command}")
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"PowerShell error: {e}") from e
