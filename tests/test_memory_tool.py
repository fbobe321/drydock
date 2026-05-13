"""Tests for the memory tool — save / recall / list_keys / forget / stats.

All tests use a tmp_path-backed DRYDOCK_MEMORY_PATH so they don't touch
the real ~/.drydock/agent_memory/ store.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from drydock.core.tools.builtins.memory_tool import (
    Memory,
    MemoryArgs,
    MemoryResult,
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    p = tmp_path / "notes.jsonl"
    monkeypatch.setenv("DRYDOCK_MEMORY_PATH", str(p))
    return p


async def _run(**kwargs) -> MemoryResult:
    args = MemoryArgs(**kwargs)
    tool = Memory.__new__(Memory)
    tool.config = type("_C", (), {"permission": None})()
    out: MemoryResult | None = None
    async for ev in tool.run(args):
        if isinstance(ev, MemoryResult):
            out = ev
    assert out is not None
    return out


# ============================================================================
# Save
# ============================================================================

class TestSave:
    @pytest.mark.asyncio
    async def test_save_creates_file(self, store):
        out = await _run(op="save", key="k1", value="v1")
        assert out.ok and out.saved
        assert store.is_file()
        assert "k1" in store.read_text()
        assert "v1" in store.read_text()

    @pytest.mark.asyncio
    async def test_save_with_tags(self, store):
        out = await _run(op="save", key="k1", value="v1", tags="a,b,c")
        assert out.ok
        contents = store.read_text()
        assert '"a"' in contents and '"b"' in contents and '"c"' in contents

    @pytest.mark.asyncio
    async def test_save_missing_key(self, store):
        out = await _run(op="save", value="v1")
        assert not out.ok and "key" in out.error.lower()

    @pytest.mark.asyncio
    async def test_save_missing_value(self, store):
        out = await _run(op="save", key="k1")
        assert not out.ok and "value" in out.error.lower()

    @pytest.mark.asyncio
    async def test_save_value_too_long(self, store):
        out = await _run(op="save", key="k1", value="x" * 9000)
        assert not out.ok and "too long" in out.error.lower()


# ============================================================================
# Recall
# ============================================================================

class TestRecall:
    @pytest.mark.asyncio
    async def test_recall_finds_relevant(self, store):
        await _run(op="save", key="api auth",
                   value="JWT token in Authorization header, refresh via /refresh endpoint")
        await _run(op="save", key="db schema",
                   value="users table has id, email, hashed_password")
        # Query overlaps the auth note's tokens (api, auth, token, header).
        out = await _run(op="recall", query="api auth token header")
        assert out.ok
        assert len(out.hits) >= 1
        # Top hit should be the auth note, not the db one.
        assert out.hits[0].key == "api auth"

    @pytest.mark.asyncio
    async def test_recall_respects_limit(self, store):
        for i in range(10):
            await _run(op="save", key=f"note{i}", value=f"common token shared text {i}")
        out = await _run(op="recall", query="common token shared", limit=3)
        assert out.ok and len(out.hits) == 3

    @pytest.mark.asyncio
    async def test_recall_empty_store(self, store):
        out = await _run(op="recall", query="anything")
        assert out.ok and out.hits == []

    @pytest.mark.asyncio
    async def test_recall_missing_query(self, store):
        out = await _run(op="recall")
        assert not out.ok and "query" in out.error.lower()

    @pytest.mark.asyncio
    async def test_recall_no_matches_returns_empty(self, store):
        await _run(op="save", key="k1", value="completely unrelated content")
        out = await _run(op="recall", query="quantum chromodynamics")
        assert out.ok and out.hits == []

    @pytest.mark.asyncio
    async def test_recall_score_descending(self, store):
        await _run(op="save", key="exact",
                   value="api authentication jwt token header")
        await _run(op="save", key="partial",
                   value="api stuff and other things")
        out = await _run(op="recall", query="api authentication jwt token")
        assert out.ok and len(out.hits) >= 2
        assert out.hits[0].score >= out.hits[1].score


# ============================================================================
# list_keys
# ============================================================================

class TestListKeys:
    @pytest.mark.asyncio
    async def test_list_dedupes(self, store):
        # Same key written twice — list should show it once.
        await _run(op="save", key="k1", value="v1a")
        await _run(op="save", key="k1", value="v1b")
        await _run(op="save", key="k2", value="v2")
        out = await _run(op="list_keys")
        assert out.ok
        assert sorted(out.keys) == ["k1", "k2"]

    @pytest.mark.asyncio
    async def test_list_empty(self, store):
        out = await _run(op="list_keys")
        assert out.ok and out.keys == []

    @pytest.mark.asyncio
    async def test_list_respects_limit(self, store):
        for i in range(20):
            await _run(op="save", key=f"k{i}", value="v")
        out = await _run(op="list_keys", limit=5)
        assert out.ok and len(out.keys) == 5


# ============================================================================
# Forget
# ============================================================================

class TestForget:
    @pytest.mark.asyncio
    async def test_forget_removes_from_recall(self, store):
        await _run(op="save", key="k1", value="unique-marker-xyz123")
        out_before = await _run(op="recall", query="unique-marker-xyz123")
        assert out_before.hits and out_before.hits[0].key == "k1"
        await _run(op="forget", key="k1")
        out_after = await _run(op="recall", query="unique-marker-xyz123")
        assert out_after.hits == []

    @pytest.mark.asyncio
    async def test_forget_missing_key(self, store):
        out = await _run(op="forget")
        assert not out.ok

    @pytest.mark.asyncio
    async def test_forget_unknown_key_is_noop_ok(self, store):
        # Forgetting a key that doesn't exist returns ok with 0 records.
        out = await _run(op="forget", key="never_saved")
        assert out.ok and out.stats.get("forgotten_records") == 0

    @pytest.mark.asyncio
    async def test_forget_removes_from_list_keys(self, store):
        await _run(op="save", key="k1", value="v1")
        await _run(op="save", key="k2", value="v2")
        await _run(op="forget", key="k1")
        out = await _run(op="list_keys")
        assert out.ok and out.keys == ["k2"]


# ============================================================================
# Stats
# ============================================================================

class TestStats:
    @pytest.mark.asyncio
    async def test_stats_empty(self, store):
        out = await _run(op="stats")
        assert out.ok
        assert out.stats["total_records"] == 0
        assert out.stats["live_records"] == 0

    @pytest.mark.asyncio
    async def test_stats_after_writes_and_forgets(self, store):
        await _run(op="save", key="k1", value="v1")
        await _run(op="save", key="k2", value="v2")
        await _run(op="save", key="k3", value="v3")
        await _run(op="forget", key="k1")
        out = await _run(op="stats")
        assert out.ok
        # 2 originals left after k1's forget rewrite + the tombstone
        assert out.stats["live_records"] >= 2
        assert out.stats["unique_keys"] == 2


# ============================================================================
# Discovery / unknown op
# ============================================================================


def test_memory_tool_name():
    assert Memory.get_name() == "memory"


@pytest.mark.asyncio
async def test_unknown_op(store):
    # Pydantic Literal will block at construction; verify by passing a
    # valid op then mutating (simulates a misuse path).
    args = MemoryArgs(op="stats")
    tool = Memory.__new__(Memory)
    tool.config = type("_C", (), {"permission": None})()
    args.op = "garbage"  # type: ignore[assignment]
    out: MemoryResult | None = None
    async for ev in tool.run(args):
        if isinstance(ev, MemoryResult):
            out = ev
    assert out is not None
    assert not out.ok
