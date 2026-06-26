"""The streamed-text runaway-repetition guard (loop_detect.runaway_repetition_len).

Detects the rare failure where a weak local model collapses into repeating one
short unit hundreds of times (gemma4 emitted `295:` ~1365× on make-mips). Must
fire on real collapses but NEVER on legitimate repeated content.
"""
from __future__ import annotations

from drydock.loop_detect import runaway_repetition_len


# ── fires on real collapses ────────────────────────────────────────────────

def test_detects_repeated_line_collapse():
    text = "Here is the output:\n" + "295:\n" * 1365
    run = runaway_repetition_len(text)
    assert run > 0
    # The non-repeating prefix must survive a trim of exactly `run` chars.
    assert text[: len(text) - run].startswith("Here is the output:")


def test_detects_repeated_token_without_newlines():
    text = "blah " + "ha" * 1000
    assert runaway_repetition_len(text) > 0


def test_detects_repeated_word_unit():
    text = "intro\n" + "the same thing over and over " * 60
    assert runaway_repetition_len(text) > 0


# ── does NOT fire on legitimate content ────────────────────────────────────

def test_short_repetition_is_fine():
    # A handful of identical short lines is normal, not a collapse.
    assert runaway_repetition_len("foo\n" * 10) == 0


def test_horizontal_rule_is_fine():
    text = "## Section\n" + "-" * 80 + "\nbody text here, perfectly normal."
    assert runaway_repetition_len(text) == 0


def test_normal_prose_is_fine():
    text = (
        "I fixed the bug by reordering the messages so a user turn never "
        "follows a tool result. I added a regression test and ran the suite; "
        "all 252 tests pass. Let me know if you want anything else.\n"
    ) * 3
    assert runaway_repetition_len(text) == 0


def test_blank_lines_do_not_trip_it():
    # Many blank lines (whitespace-only unit) must be ignored.
    assert runaway_repetition_len("text\n" + "\n" * 500) == 0


def test_empty_and_short_text():
    assert runaway_repetition_len("") == 0
    assert runaway_repetition_len("short") == 0


def test_code_with_repeated_short_lines_is_fine():
    # Real code often repeats short lines (e.g. closing braces); a dozen is fine.
    text = "def f():\n" + "    pass\n" * 12
    assert runaway_repetition_len(text) == 0


# ── integration: the real stream() path trims + stops a collapse ────────────

import drydock.providers as P  # noqa: E402
from drydock.providers import AssistantTurn, TextChunk, stream  # noqa: E402


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


def test_stream_trims_and_stops_a_collapse(monkeypatch):
    # A normal opener, then the model collapses into '295:\n' a thousand times.
    chunks = [_Chunk("Here's the trace:\n")] + [_Chunk("295:\n") for _ in range(1000)]
    monkeypatch.setattr(P, "_create_abortable", lambda *a, **k: iter(chunks))

    full = ""
    streamed = []
    for ev in stream(
        model="gemma4", system="s",
        messages=[{"role": "user", "content": "go"}],
        tool_schemas=[], config={"model": "gemma4", "provider": "vllm"},
    ):
        if isinstance(ev, TextChunk):
            streamed.append(ev.text)
        elif isinstance(ev, AssistantTurn):
            full = ev.text

    # Stored turn keeps the real prefix, is trimmed (NOT all 1000 reps), and
    # carries the stop marker.
    assert full.startswith("Here's the trace:")
    assert "stopped by drydock" in full
    assert full.count("295:") < 500           # the runaway tail was trimmed off
    assert any("began repeating" in s for s in streamed)  # user saw the notice


def test_stream_leaves_normal_output_untouched(monkeypatch):
    chunks = [_Chunk("All "), _Chunk("done — "), _Chunk("tests pass.")]
    monkeypatch.setattr(P, "_create_abortable", lambda *a, **k: iter(chunks))
    full = ""
    for ev in stream(model="gemma4", system="s",
                     messages=[{"role": "user", "content": "go"}],
                     tool_schemas=[], config={"model": "gemma4", "provider": "vllm"}):
        if isinstance(ev, AssistantTurn):
            full = ev.text
    assert full == "All done — tests pass."
    assert "stopped by drydock" not in full
