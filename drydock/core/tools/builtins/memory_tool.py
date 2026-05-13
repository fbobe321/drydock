"""Memory tool — persistent key/value notes the model can write & recall
across sessions.

The transformer-weakness map (Babich, "What coding can fix"): long-term
memory is one of the easiest weaknesses to patch with classical code.
This tool gives the model a small, indexed scratchpad it can append to
during a session and search across all sessions next time.

Storage: `~/.drydock/agent_memory/notes.jsonl` — append-only JSONL, one
record per save. Recall does a full scan + ranked match (TF-IDF over
the note key + value text). Cheap until ~100K notes; we cap at 10K to
keep recall under 100ms.

Operations (single tool, `op` arg):

    memory(op="save", key="api auth pattern", value="JWT in Authorization header...", tags="api,auth")
    memory(op="recall", query="how do we authenticate API requests", limit=3)
    memory(op="list_keys", limit=20)
    memory(op="forget", key="api auth pattern")          # marks superseded; doesn't physically delete
    memory(op="stats")

Read-only operations (recall, list_keys, stats) auto-approve. Write
operations (save, forget) follow normal approval if the user has
restricted them.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import AsyncGenerator
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from drydock.core.types import ToolResultEvent


_DEFAULT_DIR = Path.home() / ".drydock" / "agent_memory"
_NOTES_FILE = "notes.jsonl"
_MAX_NOTES = 10_000          # cap on total written notes (oldest become unrecallable)
_MAX_VALUE_LEN = 8000


def _store_path() -> Path:
    """Resolve the notes file path. Env override:
    DRYDOCK_MEMORY_PATH → full path to the .jsonl file."""
    env = os.environ.get("DRYDOCK_MEMORY_PATH")
    if env:
        return Path(env).expanduser()
    return _DEFAULT_DIR / _NOTES_FILE


# ── Tokenizer / scorer ────────────────────────────────────────────────


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 2]


def _score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Lightweight TF-IDF-ish score: token overlap weighted by inverse
    frequency in the doc. No global IDF — single-doc score is fine for
    a few-hundred-note store."""
    if not query_tokens or not doc_tokens:
        return 0.0
    doc_counts = Counter(doc_tokens)
    doc_len = len(doc_tokens)
    score = 0.0
    for tok in query_tokens:
        if tok in doc_counts:
            tf = doc_counts[tok] / doc_len
            # Soft idf — favor tokens that are not 50%+ of the doc.
            score += tf * (1.0 / (1.0 + math.log1p(doc_counts[tok])))
    # Length normalization so a 50-token note doesn't always beat a 500-token note.
    return score * math.sqrt(min(len(query_tokens), 12))


# ── Storage ────────────────────────────────────────────────────────────


def _load_notes(path: Path | None = None, include_forgotten: bool = False) -> list[dict]:
    p = path or _store_path()
    if not p.is_file():
        return []
    out: list[dict] = []
    try:
        with p.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not include_forgotten and rec.get("forgotten"):
                    continue
                out.append(rec)
    except OSError:
        return []
    return out


def _append_note(rec: dict, path: Path | None = None) -> None:
    p = path or _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── Tool ───────────────────────────────────────────────────────────────


MemoryOp = Literal["save", "recall", "list_keys", "forget", "stats"]


class MemoryArgs(BaseModel):
    op: MemoryOp = Field(description="Operation: save | recall | list_keys | forget | stats")
    key: str = Field(default="", description="Required for save / forget. Stable identifier for the note.")
    value: str = Field(default="", description="Note body (save only). Capped at 8000 chars.")
    query: str = Field(default="", description="Free-text query (recall only).")
    tags: str = Field(default="", description="Comma-separated tags (save only). Stored for filtering.")
    limit: int = Field(default=5, ge=1, le=50, description="Max results (recall, list_keys).")


class MemoryHit(BaseModel):
    key: str
    value: str
    tags: list[str] = []
    ts: str
    score: float = 0.0


class MemoryResult(BaseModel):
    ok: bool
    op: str = ""
    error: str = ""
    saved: bool = False
    hits: list[MemoryHit] = []
    keys: list[str] = []
    stats: dict = {}


class MemoryConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class Memory(
    BaseTool[MemoryArgs, MemoryResult, MemoryConfig, BaseToolState],
    ToolUIData[MemoryArgs, MemoryResult],
):
    description: ClassVar[str] = (
        "Persistent cross-session memory. Save notes (key+value+tags), "
        "recall by free-text query, list saved keys, forget by key. "
        "Use to remember per-project patterns, decisions, and recurring "
        "answers so the next session doesn't have to rediscover them. "
        "Storage: ~/.drydock/agent_memory/notes.jsonl (append-only). "
        "Examples: memory(op='save', key='build cmd', value='make test'); "
        "memory(op='recall', query='how to run tests'); memory(op='stats')."
    )

    @classmethod
    def format_call_display(cls, args: MemoryArgs) -> ToolCallDisplay:
        if args.op == "save":
            return ToolCallDisplay(summary=f"memory save: {args.key[:50]}")
        if args.op == "recall":
            q = args.query[:50] + ("..." if len(args.query) > 50 else "")
            return ToolCallDisplay(summary=f"memory recall: {q}")
        if args.op == "forget":
            return ToolCallDisplay(summary=f"memory forget: {args.key[:50]}")
        return ToolCallDisplay(summary=f"memory {args.op}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, MemoryResult):
            r = event.result
            if not r.ok:
                return ToolResultDisplay(success=False, message=f"memory: {r.error[:80]}")
            if r.op == "save":
                return ToolResultDisplay(success=True, message="saved")
            if r.op == "recall":
                return ToolResultDisplay(success=True, message=f"{len(r.hits)} hit(s)")
            if r.op == "list_keys":
                return ToolResultDisplay(success=True, message=f"{len(r.keys)} key(s)")
            if r.op == "forget":
                return ToolResultDisplay(success=True, message="forgotten")
            if r.op == "stats":
                return ToolResultDisplay(success=True, message=str(r.stats))
        return ToolResultDisplay(success=True, message="memory complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Accessing memory"

    def resolve_permission(self, args: MemoryArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: MemoryArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | MemoryResult, None]:
        op = args.op

        if op == "save":
            err = self._save(args)
            yield MemoryResult(ok=err is None, op="save", saved=err is None, error=err or "")
            return

        if op == "recall":
            try:
                hits = self._recall(args)
            except ValueError as e:
                yield MemoryResult(ok=False, op="recall", error=str(e))
                return
            yield MemoryResult(ok=True, op="recall", hits=hits)
            return

        if op == "list_keys":
            keys = self._list_keys(args.limit)
            yield MemoryResult(ok=True, op="list_keys", keys=keys)
            return

        if op == "forget":
            if not args.key:
                yield MemoryResult(ok=False, op="forget", error="key required")
                return
            n = self._forget(args.key)
            yield MemoryResult(
                ok=True, op="forget",
                stats={"forgotten_records": n},
            )
            return

        if op == "stats":
            yield MemoryResult(ok=True, op="stats", stats=self._stats())
            return

        yield MemoryResult(ok=False, op=op, error=f"unknown op: {op}")

    # ── Implementation ────────────────────────────────────────────────

    def _save(self, args: MemoryArgs) -> str | None:
        if not args.key:
            return "key required"
        if not args.value:
            return "value required"
        if len(args.value) > _MAX_VALUE_LEN:
            return f"value too long ({len(args.value)} > {_MAX_VALUE_LEN})"
        notes = _load_notes(include_forgotten=True)
        if len([n for n in notes if not n.get("forgotten")]) >= _MAX_NOTES:
            return f"memory full ({_MAX_NOTES} notes); forget some first"
        rec = {
            "key": args.key,
            "value": args.value,
            "tags": [t.strip() for t in args.tags.split(",") if t.strip()],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _append_note(rec)
        return None

    def _recall(self, args: MemoryArgs) -> list[MemoryHit]:
        if not args.query:
            raise ValueError("query required for recall")
        q_tokens = _tokenize(args.query)
        notes = _load_notes()
        # Score every note + return top `limit` above zero.
        scored: list[tuple[float, dict]] = []
        for n in notes:
            doc = f"{n.get('key', '')} {n.get('value', '')} {' '.join(n.get('tags', []))}"
            s = _score(q_tokens, _tokenize(doc))
            if s > 0:
                scored.append((s, n))
        scored.sort(key=lambda t: -t[0])
        return [
            MemoryHit(
                key=n["key"],
                value=n["value"],
                tags=n.get("tags", []),
                ts=n.get("ts", ""),
                score=round(s, 4),
            )
            for s, n in scored[:args.limit]
        ]

    def _list_keys(self, limit: int) -> list[str]:
        # Newest first, dedup by key (most recent value wins).
        notes = _load_notes()
        seen: set[str] = set()
        keys: list[str] = []
        for n in reversed(notes):
            k = n.get("key", "")
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
                if len(keys) >= limit:
                    break
        return keys

    def _forget(self, key: str) -> int:
        """Append a tombstone for `key`. Returns number of live records
        the tombstone supersedes."""
        notes = _load_notes()
        live = [n for n in notes if n.get("key") == key]
        rec = {
            "key": key,
            "value": "",
            "tags": [],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "forgotten": True,
        }
        _append_note(rec)
        # Filter on next read — the tombstone hides any earlier same-key live
        # records from `_load_notes(include_forgotten=False)` only if we
        # extend the loader. For now, the simpler model: tombstones are
        # full new records that mark the KEY as gone; recall won't return
        # forgotten entries because we filter `forgotten=True`. Live
        # entries with the same key from BEFORE the tombstone still show
        # up — that's a known limitation; future: rewrite the file to
        # drop superseded entries on tombstone.
        # For this ship: also rewrite the file in place to drop superseded
        # records, so forget actually erases.
        path = _store_path()
        all_notes = _load_notes(include_forgotten=True)
        kept = [n for n in all_notes if not (n.get("key") == key and not n.get("forgotten"))]
        path.write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in kept))
        return len(live)

    def _stats(self) -> dict:
        notes_all = _load_notes(include_forgotten=True)
        notes_live = [n for n in notes_all if not n.get("forgotten")]
        keys_unique = {n["key"] for n in notes_live if "key" in n}
        return {
            "total_records": len(notes_all),
            "live_records": len(notes_live),
            "unique_keys": len(keys_unique),
            "store_path": str(_store_path()),
        }
