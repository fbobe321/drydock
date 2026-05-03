"""Context recovery — when a tool fails for lack of context, auto-query
GraphRAG and embed the result so the agent's next attempt succeeds.

The user-visible scenario:

    Agent calls: search_replace(file_path="utils/parse.py", search="def lex(", replace="...")
    Tool returns: "search text not found"
                  → Auto-inject: retrieve(query="lex") finds it at parser/lexer.py:47
                  → "search text not found. Auto-retrieved: <result>"
    Agent retries with the right path.

This is the closed loop the user asked for: a context-failure produces
a GraphRAG solution + retry hint in one round-trip, instead of the
model thrashing on grep variants until it gives up.

Design constraints:
- ZERO new tool calls from inside another tool. We just enrich the
  result text. The agent decides what to do.
- NEVER block the original error — the recovery hint is APPENDED, the
  underlying tool result still says "not found". User trust > magic.
- Cheap: one retrieve() call max per recovery. Skip recovery if the
  index doesn't exist or returns no hits.
- Best-effort. Any exception inside recovery is swallowed (logged) so
  the original tool result is what the agent gets.

Public surface:
    recover_for_search_replace(file_path, search_text) -> str | None
    recover_for_read_file(missing_path) -> str | None
    recover_for_grep(pattern, exit_code, stdout) -> str | None
    recover_for_query(query) -> str | None      # generic
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)


def _resolve_db_path() -> Path:
    """Same resolution order as the retrieve tool (env → project → user)."""
    import os
    env = os.environ.get("DRYDOCK_GRAPHRAG_DB")
    if env:
        return Path(env).expanduser()
    project_db = Path.cwd() / ".drydock" / "graphrag.sqlite"
    if project_db.is_file():
        return project_db
    return Path.home() / ".drydock" / "graphrag.sqlite"


def _looks_like_project(path: Path) -> bool:
    markers = (".git", "pyproject.toml", "setup.py", "package.json",
               "Cargo.toml", "go.mod", "AGENTS.md", "CLAUDE.md")
    return any((path / m).exists() for m in markers)


def _safe_retrieve(query: str, *, symbol_limit: int = 3, text_limit: int = 2):
    """Run a retrieve query. Returns None on any failure (no index, no hits,
    exception). Auto-ingests the cwd if it's a project and no index exists,
    matching the retrieve tool's behavior."""
    if not query or not query.strip():
        return None
    try:
        from drydock.graphrag import Index
    except Exception as e:
        logger.debug("graphrag import failed in context_recovery: %s", e)
        return None
    try:
        db_path = _resolve_db_path()
        if not db_path.is_file():
            cwd = Path.cwd()
            if not _looks_like_project(cwd):
                return None
            db_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                Index(db_path).ingest_path(cwd)
            except Exception as e:
                logger.debug("auto-ingest failed in context_recovery: %s", e)
                return None
        idx = Index(db_path)
        result = idx.retrieve(
            query, symbol_limit=symbol_limit, text_limit=text_limit
        )
        if result.is_empty():
            return None
        return result
    except Exception as e:
        logger.debug("context_recovery retrieve raised: %s", e)
        return None


def _format_recovery_block(
    query: str, result, label: str = "Auto-retrieved context"
) -> str:
    """Render a recovery hint that the model will see appended to the
    original tool error. Keeps it compact — the agent is going to
    re-call the failing tool, we just need to point at the right spot."""
    body = result.format()
    if len(body) > 1500:
        body = body[:1500] + "\n... (truncated; call retrieve() for more)"
    return (
        f"\n\n[{label}: GraphRAG retrieve(query={query!r}) found:]\n{body}"
    )


# ---------------------------------------------------------------------
# Per-tool recovery helpers
# ---------------------------------------------------------------------

_SYMBOL_RE = re.compile(r"def\s+(\w+)|class\s+(\w+)|(\w+)\(")


def recover_for_search_replace(
    file_path: str, search_text: str
) -> str | None:
    """search_replace failed to find its SEARCH text. Look up by:
    1. The file's basename (best for "wrong package" failures)
    2. The first symbol-like token in the SEARCH text"""
    queries: list[str] = []
    if file_path:
        stem = Path(file_path).stem
        if stem and stem not in ("__init__", "main"):
            queries.append(stem)
    if search_text:
        for m in _SYMBOL_RE.finditer(search_text[:300]):
            sym = next((g for g in m.groups() if g), None)
            if sym and len(sym) > 2 and sym not in queries:
                queries.append(sym)
                if len(queries) >= 2:
                    break
    for q in queries:
        result = _safe_retrieve(q)
        if result is not None:
            return _format_recovery_block(q, result, label="Recovery")
    return None


def recover_for_read_file(missing_path: str) -> str | None:
    """read_file got a path that doesn't exist. Look up by basename —
    the file may live in a sibling package the model didn't anticipate."""
    if not missing_path:
        return None
    stem = Path(missing_path).stem
    if not stem or stem in ("__init__", "main"):
        return None
    result = _safe_retrieve(stem)
    if result is None:
        return None
    return _format_recovery_block(stem, result, label="Recovery")


_GREP_QUERY_SKIP = frozenset({
    # Python / regex keywords that are too generic for a useful retrieve.
    "def", "class", "import", "from", "return", "self", "true", "false",
    "none", "null", "and", "not", "for", "the", "with", "this", "that",
    "var", "let", "const", "function", "async", "await",
})


def recover_for_grep(
    pattern: str, exit_code: int, stdout: str
) -> str | None:
    """grep returned no matches (rc=1) or empty output. Use the most
    specific bare word from the regex as a query (skips keywords like
    `def`/`class` that would match too much)."""
    if exit_code == 0 and stdout.strip():
        return None  # had results — no recovery needed
    bare = re.sub(r"[^\w\s]", " ", pattern or "")
    candidates = [
        w for w in bare.split()
        if len(w) >= 3 and w.lower() not in _GREP_QUERY_SKIP
    ]
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    query = candidates[0]
    result = _safe_retrieve(query, symbol_limit=5, text_limit=3)
    if result is None:
        return None
    return _format_recovery_block(query, result, label="Recovery")


def recover_for_query(query: str) -> str | None:
    """Generic recovery — caller knows what to query for."""
    result = _safe_retrieve(query)
    if result is None:
        return None
    return _format_recovery_block(query, result, label="Recovery")
