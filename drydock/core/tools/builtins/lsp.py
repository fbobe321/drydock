"""LSP tool — Language Server Protocol integration.

Provides type checking, go-to-definition, find-references,
and symbol listing via LSP servers (pyright, tsserver, etc.).
"""

from __future__ import annotations

import asyncio
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


class LspArgs(BaseModel):
    action: str = Field(description="Action: 'diagnostics', 'definition', 'references', 'symbols', 'hover'")
    file: str = Field(description="File path")
    line: int = Field(default=0, description="Line number (0-based, for definition/references/hover)")
    column: int = Field(default=0, description="Column number (0-based)")
    symbol: str = Field(default="", description="Symbol name (for symbols action)")


class LspResult(BaseModel):
    action: str
    results: list[dict]
    count: int
    error: str = ""


class LspConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    pyright_path: str = "pyright"
    timeout: int = 30


class Lsp(
    BaseTool[LspArgs, LspResult, LspConfig, BaseToolState],
    ToolUIData[LspArgs, LspResult],
):
    description: ClassVar[str] = (
        "Language server integration: type checking, go-to-definition, "
        "find-references, list symbols. Requires pyright or similar LSP."
    )

    @classmethod
    def format_call_display(cls, args: LspArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"lsp: {args.action} {args.file}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, LspResult):
            return ToolResultDisplay(success=True, message=f"{event.result.count} results")
        return ToolResultDisplay(success=True, message="LSP complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Running LSP"

    def resolve_permission(self, args: LspArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: LspArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspResult, None]:
        file_path = Path(args.file).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path

        if not file_path.exists():
            raise ToolError(f"File not found: {file_path}")

        match args.action:
            case "diagnostics":
                yield await self._diagnostics(file_path)
            case "definition":
                yield await self._definition(file_path, args.line, args.column)
            case "references":
                yield await self._references(file_path, args.line, args.column, args.symbol)
            case "symbols":
                yield await self._symbols(file_path)
            case "hover":
                yield await self._hover(file_path, args.line, args.column)
            case _:
                raise ToolError(f"Unknown LSP action: {args.action}. Use: diagnostics, definition, references, symbols, hover")

    async def _diagnostics(self, file_path: Path) -> LspResult:
        """Run type checking on a file using pyright."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.config.pyright_path, "--outputjson", str(file_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.config.timeout)
            data = json.loads(stdout.decode())
            diags = []
            for d in data.get("generalDiagnostics", []):
                diags.append({
                    "file": d.get("file", str(file_path)),
                    "line": d.get("range", {}).get("start", {}).get("line", 0),
                    "severity": d.get("severity", "error"),
                    "message": d.get("message", ""),
                })
            return LspResult(action="diagnostics", results=diags[:50], count=len(diags))
        except FileNotFoundError:
            return LspResult(action="diagnostics", results=[], count=0,
                           error=f"pyright not found. Install: pip install pyright")
        except Exception as e:
            return LspResult(action="diagnostics", results=[], count=0, error=str(e))

    async def _definition(self, file_path: Path, line: int, col: int) -> LspResult:
        """Find definition of symbol at position (uses grep as fallback)."""
        # Read the line to get the symbol
        try:
            lines = file_path.read_text().split("\n")
            if line < len(lines):
                text = lines[line]
                # Extract word at column
                import re
                words = re.findall(r'\b\w+\b', text)
                symbol = ""
                pos = 0
                for w in words:
                    idx = text.find(w, pos)
                    if idx <= col <= idx + len(w):
                        symbol = w
                        break
                    pos = idx + len(w)

                if symbol:
                    # grep for definition
                    proc = await asyncio.create_subprocess_exec(
                        "grep", "-rn", f"def {symbol}\\|class {symbol}", str(file_path.parent),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                    results = []
                    for hit in stdout.decode().strip().split("\n")[:10]:
                        if hit:
                            results.append({"location": hit})
                    return LspResult(action="definition", results=results, count=len(results))
        except Exception as e:
            return LspResult(action="definition", results=[], count=0, error=str(e))

        return LspResult(action="definition", results=[], count=0)

    async def _references(self, file_path: Path, line: int, col: int, symbol: str) -> LspResult:
        """Find all references to a symbol."""
        if not symbol:
            return LspResult(action="references", results=[], count=0, error="No symbol specified")

        try:
            proc = await asyncio.create_subprocess_exec(
                "grep", "-rn", f"\\b{symbol}\\b", str(file_path.parent),
                "--include=*.py",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            results = []
            for hit in stdout.decode().strip().split("\n")[:30]:
                if hit:
                    results.append({"reference": hit})
            return LspResult(action="references", results=results, count=len(results))
        except Exception as e:
            return LspResult(action="references", results=[], count=0, error=str(e))

    async def _symbols(self, file_path: Path) -> LspResult:
        """List symbols (functions, classes) in a file."""
        try:
            content = file_path.read_text()
            import re
            symbols = []
            for i, line in enumerate(content.split("\n")):
                if match := re.match(r'^(class|def|async def)\s+(\w+)', line):
                    symbols.append({
                        "kind": match.group(1),
                        "name": match.group(2),
                        "line": i,
                    })
            return LspResult(action="symbols", results=symbols, count=len(symbols))
        except Exception as e:
            return LspResult(action="symbols", results=[], count=0, error=str(e))

    async def _hover(self, file_path: Path, line: int, col: int) -> LspResult:
        """Get type info for symbol at position."""
        # Fallback: parse the file for type annotations
        try:
            lines = file_path.read_text().split("\n")
            if line < len(lines):
                return LspResult(action="hover", results=[{"line": lines[line].strip()}], count=1)
        except Exception:
            pass
        return LspResult(action="hover", results=[], count=0)
