"""Tests for model-specific tuning (Gemma accommodations)."""
from __future__ import annotations

from drydock.tuning import (
    GEMMA_DISABLED_TOOLS,
    extract_thinking,
    filter_tool_schemas,
    is_gemma,
    strip_thinking_tokens,
    system_prompt_for_model,
    thinking_level_for_turn,
    use_streaming,
)


def test_is_gemma():
    assert is_gemma("gemma4")
    assert is_gemma("Gemma-4-26B-A4B-it-UD-Q3_K_M.gguf")
    assert not is_gemma("gpt-4o")
    assert not is_gemma("")
    assert not is_gemma(None)


def test_strip_thinking_tokens_removes_marked_span():
    assert strip_thinking_tokens("a<|channel>secret<channel|>b") == "ab"


def test_strip_thinking_tokens_multiline_span():
    assert strip_thinking_tokens("x<|channel>line1\nline2<channel|>y") == "xy"


def test_strip_thinking_tokens_bare_markers():
    assert strip_thinking_tokens("a<|channel>b") == "ab"
    assert strip_thinking_tokens("a<channel|>b") == "ab"


def test_strip_thinking_tokens_noop_when_absent():
    assert strip_thinking_tokens("hello world") == "hello world"
    assert strip_thinking_tokens("") == ""


def test_strip_thinking_channel_closing_with_tool_call_marker():
    # A thought running into a malformed call closes with <tool_call|>.
    assert strip_thinking_tokens("a<|channel>thought<tool_call|>b") == "ab"


def test_strip_generic_special_tokens():
    assert strip_thinking_tokens('path<|"|>.py') == "path.py"
    assert strip_thinking_tokens("x<|start|>y<|end|>z") == "xyz"
    assert strip_thinking_tokens("a<|endoftext|>b") == "ab"


def test_strip_does_not_mangle_normal_code():
    # Real code with comparisons / bitwise ops must be untouched.
    for src in [
        "if a < b | c > d: pass",
        "x = [1, 2, 3]\nprint(x)",
        "result = func(a, b) > 0",
        "# divider =======================",
    ]:
        assert strip_thinking_tokens(src) == src


def test_use_streaming_off_for_gemma_with_tools():
    assert use_streaming("gemma4", has_tools=True) is False
    # text-only turn may stream even on gemma
    assert use_streaming("gemma4", has_tools=False) is True
    # non-gemma always streams
    assert use_streaming("gpt-4o", has_tools=True) is True


def test_filter_tool_schemas_gates_only_for_gemma():
    schemas = [{"name": "Read"}, {"name": "ask_user_question"}, {"name": "Write"}]
    gated = filter_tool_schemas(schemas, "gemma4")
    names = {s["name"] for s in gated}
    assert "ask_user_question" not in names  # still a known looper
    assert names == {"Read", "Write"}
    # `todo` is NOT gated anymore (the simple checklist tool, re-enabled v3.0.13)
    assert "todo" in {s["name"] for s in filter_tool_schemas(
        [{"name": "todo"}], "gemma4")}
    # non-gemma: untouched
    assert filter_tool_schemas(schemas, "gpt-4o") == schemas


def test_gemma_disabled_tools_are_known_loopers():
    assert "ask_user_question" in GEMMA_DISABLED_TOOLS
    assert "tool_search" in GEMMA_DISABLED_TOOLS


def test_system_prompt_selection():
    assert "ACT IMMEDIATELY" in system_prompt_for_model("gemma4")
    assert "ACT IMMEDIATELY" not in system_prompt_for_model("gpt-4o")


def test_thinking_level_adaptive():
    assert thinking_level_for_turn(1, is_user_turn=True) == "high"
    assert thinking_level_for_turn(1, is_user_turn=False) == "high"
    assert thinking_level_for_turn(5, is_user_turn=False) == "off"
    assert thinking_level_for_turn(5, is_user_turn=True) == "high"


# --- extract_thinking ---

def test_extract_thinking_no_block():
    thinking, text = extract_thinking("hello world")
    assert thinking == ""
    assert text == "hello world"


def test_extract_thinking_empty_string():
    thinking, text = extract_thinking("")
    assert thinking == ""
    assert text == ""


def test_extract_thinking_extracts_and_strips():
    raw = "<|channel>my thought<channel|>the answer"
    thinking, text = extract_thinking(raw)
    assert thinking == "my thought"
    assert text == "the answer"


def test_extract_thinking_multiline_block():
    raw = "<|channel>line1\nline2<channel|>result"
    thinking, text = extract_thinking(raw)
    assert thinking == "line1\nline2"
    assert text == "result"


def test_extract_thinking_tool_call_closer():
    # Thought running into a malformed tool call closes with <tool_call|>.
    raw = "<|channel>secret thought<tool_call|>visible"
    thinking, text = extract_thinking(raw)
    assert thinking == "secret thought"
    assert text == "visible"


def test_extract_thinking_multiple_blocks():
    raw = "<|channel>thought1<channel|>text1<|channel>thought2<channel|>text2"
    thinking, text = extract_thinking(raw)
    assert "thought1" in thinking
    assert "thought2" in thinking
    assert "text1" in text
    assert "text2" in text
    assert "<|channel>" not in text


def test_extract_thinking_noop_when_no_marker():
    raw = "no markers here <b>html</b>"
    thinking, text = extract_thinking(raw)
    assert thinking == ""
    assert text == raw
