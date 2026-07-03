"""Agent-side vision: the ViewImage tool + its API-boundary attachment.
The agent can choose to SEE an image it discovers (not just user-referenced
ones); the image rides back on the ViewImage tool result."""
from __future__ import annotations

import struct
import zlib

import drydock.tools  # noqa: F401 — triggers tool registration
from drydock import tool_registry as reg
from drydock.providers import messages_to_openai


def _png(path):
    w = h = 8
    raw = b"".join(b"\x00" + bytes([10, 20, 200]) * w for _ in range(h))
    def ch(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
    return str(path)


def test_viewimage_validates(tmp_path):
    _png(tmp_path / "a.png")
    cfg = {"cwd": str(tmp_path)}
    assert "now visible to you" in reg.execute("ViewImage", {"path": "a.png"}, cfg)
    assert "no image file" in reg.execute("ViewImage", {"path": "missing.png"}, cfg)
    assert "not a supported image" in reg.execute("ViewImage", {"path": str(tmp_path / "x.txt")}, cfg)
    assert "needs a `path`" in reg.execute("ViewImage", {}, cfg)


def test_viewimage_is_read_only_and_subagent_visible():
    td = reg.get("ViewImage")
    assert td.read_only is True
    from drydock.tools import SUBAGENT_TOOLS
    assert "ViewImage" in SUBAGENT_TOOLS


def test_viewimage_result_becomes_multimodal(tmp_path):
    _png(tmp_path / "shot.png")
    cfg = {"cwd": str(tmp_path)}
    res = reg.execute("ViewImage", {"path": "shot.png"}, cfg)
    out = messages_to_openai([{"role": "tool", "tool_call_id": "c1",
                               "name": "ViewImage", "content": res}], "sys")
    content = out[-1]["content"]
    assert isinstance(content, list)
    assert any(b.get("type") == "image_url" for b in content)
    assert content[0]["type"] == "text"


def test_non_viewimage_tool_with_png_path_stays_text(tmp_path):
    img = _png(tmp_path / "shot.png")
    out = messages_to_openai([{"role": "tool", "tool_call_id": "c2",
                               "name": "Grep", "content": f"found {img} in code"}], "sys")
    assert isinstance(out[-1]["content"], str)   # no accidental image ballooning


def test_read_on_image_points_to_viewimage(tmp_path):
    img = _png(tmp_path / "pic.png")
    out = reg.execute("Read", {"file_path": img}, {"cwd": str(tmp_path)})
    assert "ViewImage" in out and "image" in out.lower()
    # a normal text file still reads normally
    (tmp_path / "f.txt").write_text("hello\nworld\n")
    txt = reg.execute("Read", {"file_path": str(tmp_path / "f.txt")}, {"cwd": str(tmp_path)})
    assert "hello" in txt and "ViewImage" not in txt


def test_image_load_error_classifier():
    from drydock.compaction import is_image_load_error
    assert is_image_load_error("Error code: 400 - {'error': {'message': 'Failed to load image or audio file'}}")
    assert is_image_load_error("invalid_request_error: could not decode image")
    assert not is_image_load_error("rate limit exceeded")
    assert not is_image_load_error("context_length_exceeded")


def test_viewimage_nudges_over_ocr_for_documents():
    """The tool description + both system prompts steer the model to ViewImage
    (its own vision) for reading text off invoices/receipts/scans, instead of
    reaching for OCR tools first (observed on the financial-doc tbench task)."""
    from drydock.tuning import system_prompt_for_model
    desc = reg.get("ViewImage").schema["description"]
    assert "OCR" in desc and ("invoice" in desc or "receipt" in desc)
    for model in ("gemma4", "some-other-model"):
        p = system_prompt_for_model(model)
        assert "OCR" in p and "ViewImage" in p
