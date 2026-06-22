"""Streaming must preserve whitespace-only (newline) chunks.

A `.strip()` guard in the streaming loop used to DROP a chunk that was only
"\n\n" — the blank line a model streams between markdown blocks — so a long
response rendered as "paragraph### Header" mashed together (operator-reported).
"""
from __future__ import annotations

import drydock.providers as P
from drydock.providers import AssistantTurn, TextChunk, stream


class _Delta:
    def __init__(self, c):
        self.content = c
        self.tool_calls = None


class _Choice:
    def __init__(self, c):
        self.delta = _Delta(c)
        self.finish_reason = None


class _Chunk:
    def __init__(self, c):
        self.choices = [_Choice(c)]
        self.usage = None


def test_streaming_preserves_newline_only_chunks(monkeypatch):
    chunks = [
        _Chunk("Intro paragraph."),
        _Chunk("\n\n"),          # ← blank line between blocks (was dropped)
        _Chunk("### Header"),
        _Chunk("\n\n"),
        _Chunk("body text"),
    ]
    monkeypatch.setattr(P, "_create_abortable", lambda *a, **k: iter(chunks))

    full = ""
    streamed = []
    for ev in stream(
        model="gemma4", system="s",
        messages=[{"role": "user", "content": "review"}],
        tool_schemas=[],  # no tools → text-only → streaming path even for gemma
        config={"model": "gemma4", "provider": "vllm"},
    ):
        if isinstance(ev, TextChunk):
            streamed.append(ev.text)
        elif isinstance(ev, AssistantTurn):
            full = ev.text

    assert "\n\n" in "".join(streamed)            # newline chunks survived streaming
    assert "Intro paragraph.\n\n### Header" in full  # blocks not mashed
    assert "### Header\n\nbody text" in full


def test_streaming_still_drops_chunk_emptied_by_stripping(monkeypatch):
    # A chunk that is ONLY a thinking marker strips to "" and is skipped (so we
    # don't emit empty text), but real whitespace is kept.
    chunks = [_Chunk("hello"), _Chunk("<|channel>x<channel|>"), _Chunk("\n"), _Chunk("world")]
    monkeypatch.setattr(P, "_create_abortable", lambda *a, **k: iter(chunks))
    full = ""
    for ev in stream(model="gemma4", system="s",
                     messages=[{"role": "user", "content": "hi"}],
                     tool_schemas=[], config={"model": "gemma4", "provider": "vllm"}):
        if isinstance(ev, AssistantTurn):
            full = ev.text
    assert full == "hello\nworld"  # marker-only chunk gone, newline kept
