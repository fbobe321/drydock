"""Retrieve tool — query the project's GraphRAG index from the agent loop.

Wraps `drydock.graphrag.Index` so the model can ask:

    retrieve(query="where is is_json defined")
    retrieve(query="caching strategy", text_limit=3)

The DB path resolution:
1. Explicit `db` arg if the model passed one (rare).
2. `$DRYDOCK_GRAPHRAG_DB` if set.
3. `<project_root>/.drydock/graphrag.sqlite` if present.
4. `~/.drydock/graphrag.sqlite` as the user-level fallback.

If no index exists, the tool returns a friendly nudge telling the
model how to create one (`python -m drydock.graphrag ingest .`)
rather than crashing — read-before-write enforcement style. This
keeps the tool listed in every session without breaking deployments
that haven't ingested anything yet.

Read-only, always auto-approved.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
import os
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


class RetrieveArgs(BaseModel):
    query: str = Field(
        description="Natural-language query. For symbol lookup pass a single "
                    "name (e.g., 'Request', 'is_json'); for prose retrieval "
                    "pass a sentence."
    )
    text_limit: int = Field(default=5, description="Max text-chunk hits to return.")
    symbol_limit: int = Field(default=5, description="Max symbol hits to return.")
    db: str = Field(
        default="",
        description="Override the GraphRAG SQLite path. Usually leave empty.",
    )


class RetrieveResult(BaseModel):
    """Flat shape — formatted text the agent reads, plus structured counts."""
    found: bool
    db_path: str
    formatted: str
    symbol_count: int = 0
    text_count: int = 0
    note: str = ""    # populated when no index exists


class RetrieveConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    default_text_limit: int = 5
    default_symbol_limit: int = 5


def _resolve_db_path(arg: str) -> Path:
    """Pick the GraphRAG DB path. Order: explicit arg, env, project, user."""
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("DRYDOCK_GRAPHRAG_DB")
    if env:
        return Path(env).expanduser()
    project_db = Path.cwd() / ".drydock" / "graphrag.sqlite"
    if project_db.is_file():
        return project_db
    return Path.home() / ".drydock" / "graphrag.sqlite"


# Project markers that signal "this is a real codebase worth indexing."
# We refuse to auto-ingest a directory that doesn't look like one — too
# easy to misfire on `~` or `/tmp` and waste minutes.
_PROJECT_MARKERS = (
    ".git",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "AGENTS.md",
    "CLAUDE.md",
)


def _looks_like_project(path: Path) -> bool:
    return any((path / marker).exists() for marker in _PROJECT_MARKERS)


class Retrieve(
    BaseTool[RetrieveArgs, RetrieveResult, RetrieveConfig, BaseToolState],
    ToolUIData[RetrieveArgs, RetrieveResult],
):
    description: ClassVar[str] = (
        "Query the project's GraphRAG index for code symbols (with parent-class "
        "chains across packages) and prose chunks. Use BEFORE editing a file you "
        "haven't seen, or when looking for where a symbol is defined."
    )

    @classmethod
    def format_call_display(cls, args: RetrieveArgs) -> ToolCallDisplay:
        q = args.query.strip()
        if len(q) > 60:
            q = q[:57] + "..."
        return ToolCallDisplay(summary=f"retrieve: {q}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, RetrieveResult):
            if not event.result.found:
                return ToolResultDisplay(
                    success=True, message="No GraphRAG index — see hint"
                )
            total = event.result.symbol_count + event.result.text_count
            return ToolResultDisplay(
                success=True,
                message=(
                    f"Found {event.result.symbol_count} symbols, "
                    f"{event.result.text_count} chunks ({total} hits)"
                ),
            )
        return ToolResultDisplay(success=True, message="Retrieve complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Querying GraphRAG"

    def resolve_permission(self, args: RetrieveArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: RetrieveArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | RetrieveResult, None]:
        # Lazy import — graphrag is a soft dependency on the harness side
        # (the package always exists post-v2.7.34 but we keep the import
        # local so a deployment without sqlite3 still loads the tool list).
        try:
            from drydock.graphrag import Index
        except Exception as e:
            raise ToolError(f"GraphRAG module unavailable: {e}") from e

        db_path = _resolve_db_path(args.db)
        ingest_note = ""
        if not db_path.is_file():
            # Auto-ingest the cwd if it looks like a real project. Saves the
            # user from running `python -m drydock.graphrag ingest .` first,
            # and gives the agent useful results from the very first call.
            cwd = Path.cwd()
            if _looks_like_project(cwd):
                try:
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    idx = Index(db_path)
                    counts = idx.ingest_path(cwd)
                    ingest_note = (
                        f"[Auto-indexed {counts['files']} files: "
                        f"{counts['symbols']} symbols, "
                        f"{counts['chunks']} text chunks. "
                        f"DB: {db_path}]\n\n"
                    )
                except Exception as e:
                    yield RetrieveResult(
                        found=False,
                        db_path=str(db_path),
                        formatted=(
                            f"GraphRAG auto-ingest failed: {e}\n"
                            f"Run manually: `python -m drydock.graphrag ingest .`"
                        ),
                        note="ingest_failed",
                    )
                    return
            else:
                # Not a project dir — don't blindly index $HOME / /tmp / etc.
                hint = (
                    f"No GraphRAG index at {db_path} and current directory "
                    f"({cwd}) doesn't look like a project (no .git, "
                    f"pyproject.toml, package.json, etc.).\n\n"
                    f"Create one explicitly: "
                    f"`python -m drydock.graphrag ingest <path>`"
                )
                yield RetrieveResult(
                    found=False,
                    db_path=str(db_path),
                    formatted=hint,
                    note="not_a_project",
                )
                return

        try:
            idx = Index(db_path)
            text_limit = args.text_limit or self.config.default_text_limit
            symbol_limit = args.symbol_limit or self.config.default_symbol_limit
            result = idx.retrieve(
                args.query,
                text_limit=text_limit,
                symbol_limit=symbol_limit,
            )
        except Exception as e:
            raise ToolError(f"retrieve failed: {e}") from e

        formatted = ingest_note + result.format() if ingest_note else result.format()
        yield RetrieveResult(
            found=not result.is_empty(),
            db_path=str(db_path),
            formatted=formatted,
            symbol_count=len(result.symbols),
            text_count=len(result.text),
        )
