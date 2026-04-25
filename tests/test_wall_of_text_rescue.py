"""Regression tests for issue #12: missing whitespace/newlines after reasoning.

Per-chunk wall-rescue cannot fire because each streaming chunk is below
the >200-char threshold; the final accumulated content can still be a
long no-newline blob with inline numbered lists and bold headings rolled
into one paragraph. The fix in `AssistantMessage.stop_stream` runs
`_break_walls_of_text` on the full content at stream end and replays the
rescued text into the markdown widget.

Pure-function tests verify the rescue logic. Integration with the
streaming widget is exercised in shakedown sessions.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from drydock.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    _break_walls_of_text,
)


class TestBreakWallsOfText:
    def test_short_input_unchanged(self):
        text = "Hello! How can I help?"
        assert _break_walls_of_text(text) == text

    def test_already_structured_unchanged(self):
        text = "Hello!\n\nFirst para.\n\nSecond para.\n\nThird para."
        assert _break_walls_of_text(text) == text

    def test_inline_bold_headings_get_breaks(self):
        wall = (
            "I reviewed the PRD. It describes a doc QA system with three "
            "components and an integration test suite covering TF-IDF, "
            "BM25, and CLI behaviour. **Status:** ready to build. "
            "**Next:** start with the indexer module. **Tests:** unit "
            "per file with shared fixtures."
        )
        out = _break_walls_of_text(wall)
        # Wall-of-text rescue should add at least one paragraph break
        # before each **Heading:**.
        assert out.count("\n") >= 3
        assert "\n\n**Status:**" in out
        assert "\n\n**Next:**" in out
        assert "\n\n**Tests:**" in out

    def test_inline_numbered_list_gets_breaks(self):
        # Must be longer than 200 chars to trigger wall-rescue.
        wall = (
            "The system has three core components that all need to be "
            "wired together in this exact order before anything works: "
            "1. Indexer 2. Searcher 3. CLI frontend. The indexer reads "
            "markdown files. The searcher uses BM25 ranking. The CLI "
            "exposes a REPL."
        )
        out = _break_walls_of_text(wall)
        assert out.count("\n") >= 2

    def test_long_flat_prose_gets_sentence_breaks(self):
        # No inline markers, just sentences. Wall-rescue uses sentence
        # boundaries when nothing else fires (>250 char threshold).
        wall = (
            "I reviewed the PRD carefully. It describes a complete document "
            "question answering system. The architecture has clear "
            "separation of concerns. Each component is independently "
            "testable. The CLI exposes a simple interactive REPL. "
            "Indexing happens incrementally on file changes. Search uses "
            "BM25 ranking with TF-IDF fallback. All tests are unit tests."
        )
        out = _break_walls_of_text(wall)
        # Should have inserted sentence-level paragraph breaks.
        assert out.count("\n") > 0
        assert out != wall

    def test_existing_newlines_preserved(self):
        text = "Already\nhas\nbreaks. " * 30
        # nl >= 3 disables wall-rescue.
        assert _break_walls_of_text(text) == text


class TestAssistantMessageStopStreamRescue:
    @pytest.mark.asyncio
    async def test_rescue_replays_when_content_changes(self):
        # Pre-populated AssistantMessage that has accumulated wall-of-text
        # content longer than the 200-char rescue threshold with inline
        # bold headings (the exact shape Gemma 4 emits).
        msg = AssistantMessage("")
        msg._content = (
            "I built the package and reviewed the PRD against the "
            "implementation. **Status:** ready to merge after a final "
            "review pass. **Next:** start with the indexer module so "
            "tests can be wired top-down. **Tests:** unit per file "
            "with realistic fixtures and no mocks for the integration "
            "suite. Have a look when you get a chance."
        )
        msg._markdown = MagicMock()
        msg._markdown.update = AsyncMock()
        first_stream = MagicMock()
        first_stream.stop = AsyncMock()
        msg._stream = first_stream
        new_stream = MagicMock()
        new_stream.write = AsyncMock()
        new_stream.stop = AsyncMock()
        msg._ensure_stream = MagicMock(return_value=new_stream)

        await msg.stop_stream()

        msg._markdown.update.assert_any_call("")
        new_stream.write.assert_awaited_once()
        replayed = new_stream.write.await_args.args[0]
        # Rescue must have inserted paragraph breaks before the bold
        # headings ("\n\n**Status:**" etc.).
        assert "\n\n**Status:**" in replayed
        assert "\n\n**Next:**" in replayed
        assert "\n\n**Tests:**" in replayed

    @pytest.mark.asyncio
    async def test_rescue_skipped_when_no_change(self):
        msg = AssistantMessage("")
        msg._content = "Hello! Short response."
        msg._markdown = MagicMock()
        msg._markdown.update = AsyncMock()
        stream = MagicMock()
        stream.stop = AsyncMock()
        msg._stream = stream
        msg._ensure_stream = MagicMock()

        await msg.stop_stream()

        # _markdown.update should not be called for unchanged content.
        msg._markdown.update.assert_not_called()
        # The base stop_stream runs and stops the original stream.
        stream.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rescue_skipped_when_stream_already_stopped(self):
        msg = AssistantMessage("")
        msg._content = "X" * 500  # would normally trigger
        msg._stream = None  # already stopped
        msg._markdown = MagicMock()
        msg._markdown.update = AsyncMock()

        await msg.stop_stream()

        msg._markdown.update.assert_not_called()
