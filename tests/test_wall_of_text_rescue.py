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
    _preserve_line_breaks,
)


class TestTabIndentedBlocks:
    """Regression: tab→2-space conversion combined with the paragraph-break
    pass was inserting blank lines between every tab-indented line, jumbling
    git-status output and similar tab-indented blocks."""

    def test_tab_indented_block_stays_contiguous(self):
        text = (
            "Here's git status:\n\n"
            "\tdeleted:    foo.py\n"
            "\tdeleted:    bar.py\n"
            "\tnew file:   baz.py\n\n"
            "That's all."
        )
        out = _preserve_line_breaks(text)
        assert "  deleted:    foo.py\n  deleted:    bar.py" in out
        assert "  deleted:    bar.py\n  new file:   baz.py" in out

    def test_list_with_tab_continuation_stays_tight(self):
        text = "Steps:\n- Foo\n\tsubdetail\n- Bar"
        out = _preserve_line_breaks(text)
        assert "- Foo\n  subdetail\n- Bar" in out

    def test_numbered_list_with_indented_continuation(self):
        text = "Steps:\n1. Do foo\n   detail line\n2. Do bar"
        out = _preserve_line_breaks(text)
        assert "1. Do foo\n   detail line\n2. Do bar" in out

    def test_python_indented_block_preserved(self):
        text = "Here:\n    def foo():\n        return 1\nDone."
        out = _preserve_line_breaks(text)
        assert "    def foo():\n        return 1" in out

    def test_prose_then_list_still_gets_paragraph_break(self):
        text = "Here are the items.\n- First\n- Second"
        out = _preserve_line_breaks(text)
        assert "Here are the items.\n\n- First" in out

    def test_two_normal_paragraphs_still_break(self):
        text = "First sentence here.\nSecond sentence here."
        out = _preserve_line_breaks(text)
        assert "here.\n\nSecond" in out

    def test_midline_tabs_expand_to_4_spaces(self):
        """Mid-line tabs in prose/tables would land at unpredictable
        columns under Rich's tab-stop renderer; expanding to a fixed 4
        spaces gives consistent alignment regardless of surrounding
        markdown markers."""
        text = "Results:\n\nname\tvalue\nfoo\t1\nbar\t2"
        out = _preserve_line_breaks(text)
        assert "name    value" in out
        assert "foo    1" in out
        assert "\t" not in out  # all tabs gone outside fences

    def test_fenced_code_preserves_tabs(self):
        """Tabs inside ``` fences must NOT be touched — syntax highlighter
        handles them consistently and code semantics depend on them."""
        text = "Sample:\n```python\ndef foo():\n\treturn 1\n```\nDone."
        out = _preserve_line_breaks(text)
        # The tab inside the fence is preserved.
        assert "\treturn 1" in out

    def test_inline_tabs_in_prose_normalized(self):
        text = "Step 1:\trun. Step 2:\tcheck."
        out = _preserve_line_breaks(text)
        assert "Step 1:    run" in out
        assert "\t" not in out


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

    def test_inline_atx_headers_get_breaks(self):
        """Issue #16: Q3_K_M Gemma 4 jams `### Header` markers inline."""
        wall = (
            "I reviewed the codebase and there are several layers worth "
            "describing. ### Architecture The system uses a three-tier "
            "design with clear separation. ### Components The indexer "
            "reads markdown files into the database. ### Tests Each "
            "module has its own pytest file with shared fixtures."
        )
        out = _break_walls_of_text(wall)
        assert "\n\n### Architecture" in out
        assert "\n\n### Components" in out
        assert "\n\n### Tests" in out

    def test_inline_asterisk_bullets_get_breaks(self):
        """Issue #16: `* item * item * item` runs onto one line on quantized
        Gemma 4. Insert newlines so the markdown renderer treats them as
        a real list."""
        wall = (
            "Here is the project structure overview that captures the "
            "main building blocks of the system as currently designed. "
            "* indexer.py reads markdown * searcher.py runs BM25 "
            "* cli.py exposes the REPL * tests/ is the pytest suite"
        )
        out = _break_walls_of_text(wall)
        # Each `* item` should now be on its own line (or at least
        # separated by a newline).
        bullets_with_newlines = sum(1 for line in out.split("\n") if line.strip().startswith("* "))
        assert bullets_with_newlines >= 3

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
