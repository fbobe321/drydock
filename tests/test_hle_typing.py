"""Regression: HLE prompts must reach the TUI as a single literal block.

Prior to the fix, `sk.type_message` typed char-by-char at 10ms/char,
racing Textual's input handler. In real captured sessions
(/home/bobef/.vibe/logs/session/session_20260509_015004_1fa9ac60),
the prompt `"Answer this question..."` arrived as
`"Answer th\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\nis question..."`
— 13 stray newlines injected mid-word. The model then grinds in
tool-call loops and never emits `FINAL ANSWER:`, so 20/20 of the
phase1_steered run had empty predictions.

Post-fix, `_send_prompt_as_paste` wraps the prompt in xterm bracketed-
paste markers and writes once. The Textual Input widget treats the
contents as a literal block — no paste-detection, no per-char races.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HLE_EVAL = REPO / "scripts" / "hle_eval.py"


def _load_hle_eval():
    spec = importlib.util.spec_from_file_location("hle_eval_under_test", HLE_EVAL)
    mod = importlib.util.module_from_spec(spec)
    # Skip optional sk import dance — we only need _send_prompt_as_paste.
    sys.modules["hle_eval_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class _CaptureChild:
    """Minimal pexpect.spawn stand-in: records every send() call."""
    def __init__(self):
        self.sent: list[str] = []
    def send(self, text: str) -> None:
        self.sent.append(text)


def test_send_prompt_as_paste_uses_bracketed_paste_markers():
    he = _load_hle_eval()
    child = _CaptureChild()
    prompt = "Answer this question. End your response with 'FINAL ANSWER:' followed by your answer on the same line. QUESTION: What is 2+2?"

    he._send_prompt_as_paste(child, prompt)

    # Two sends: paste-block, then Enter
    assert len(child.sent) == 2, f"expected 2 sends, got {len(child.sent)}: {child.sent!r}"
    assert child.sent[0].startswith("\x1b[200~"), "paste must open with ESC[200~"
    assert child.sent[0].endswith("\x1b[201~"), "paste must close with ESC[201~"
    assert child.sent[1] == "\r", "second send must be Enter"

    # The literal prompt must appear inside the paste block, intact and
    # contiguous — no whitespace or newlines injected mid-word.
    payload = child.sent[0][len("\x1b[200~"):-len("\x1b[201~")]
    assert payload == prompt, (
        f"prompt was mangled inside paste markers:\n"
        f"  expected: {prompt!r}\n"
        f"  got:      {payload!r}"
    )


def test_send_prompt_strips_embedded_newlines():
    """Textual Input commits on Enter regardless of paste markers, so
    embedded newlines would still split the message. Strip them before
    sending — HLE prompts are single-line by `_question_prompt`'s contract."""
    he = _load_hle_eval()
    child = _CaptureChild()
    he._send_prompt_as_paste(child, "line one\nline two\rline three")
    payload = child.sent[0][len("\x1b[200~"):-len("\x1b[201~")]
    assert "\n" not in payload, f"newline leaked through: {payload!r}"
    assert "\r" not in payload, f"carriage return leaked through: {payload!r}"
    assert payload == "line one line two line three"


def test_send_prompt_does_not_use_per_char_typing():
    """Per-char typing is the bug — verify exactly two send() calls
    (paste-block, Enter), not one per character. A long prompt would
    produce 1000+ sends in the broken path."""
    he = _load_hle_eval()
    child = _CaptureChild()
    long_prompt = "x" * 2000
    he._send_prompt_as_paste(child, long_prompt)
    assert len(child.sent) == 2, (
        f"per-char typing has regressed; got {len(child.sent)} sends "
        f"for a 2000-char prompt"
    )
