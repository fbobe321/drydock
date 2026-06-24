"""Vision input: referencing an on-disk image path in a user prompt attaches it
as an OpenAI multimodal image_url block, while text-only prompts pass through as
plain strings (so display / loop-detection / compaction / token-counting, which
all assume string content, are untouched)."""
from __future__ import annotations

import struct
import zlib


from drydock.providers import _user_content_with_images, messages_to_openai


def _write_png(path, r=220, g=30, b=30, w=8, h=8):
    raw = b"".join(b"\x00" + bytes([r, g, b]) * w for _ in range(h))

    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(data)
    return str(path)


def test_text_only_passes_through_unchanged():
    assert _user_content_with_images("just plain text") == "just plain text"


def test_nonexistent_image_path_is_not_attached():
    # mentions an image-looking name that doesn't exist -> stays a string
    assert _user_content_with_images("see nope.png here") == "see nope.png here"


def test_real_image_path_becomes_multimodal(tmp_path):
    p = _write_png(tmp_path / "red.png")
    out = _user_content_with_images(f"what color is {p}?")
    assert isinstance(out, list)
    assert out[0] == {"type": "text", "text": f"what color is {p}?"}
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_quoted_path_and_jpeg_mime(tmp_path):
    p = _write_png(tmp_path / "pic.jpeg")
    out = _user_content_with_images(f'describe "{p}"')
    assert isinstance(out, list)
    assert out[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_multiple_images_deduped(tmp_path):
    p = _write_png(tmp_path / "a.png")
    out = _user_content_with_images(f"compare {p} and {p} again")
    # same path twice -> one image block (+ the text block)
    assert isinstance(out, list)
    assert sum(1 for b in out if b.get("type") == "image_url") == 1


def test_messages_to_openai_wires_image(tmp_path):
    p = _write_png(tmp_path / "x.png")
    msgs = messages_to_openai([{"role": "user", "content": f"see {p}"}], "sys")
    assert isinstance(msgs[1]["content"], list)
    assert any(b.get("type") == "image_url" for b in msgs[1]["content"])


def test_messages_to_openai_text_only_stays_string():
    msgs = messages_to_openai([{"role": "user", "content": "hello"}], "sys")
    assert msgs[1]["content"] == "hello"


def test_image_path_in_backticks_and_punctuation(tmp_path):
    # the code-from-image regression: path in markdown backticks / parens /
    # trailing punctuation must still be detected (greedy \S+ grabbed them,
    # so os.path.isfile failed and vision silently never attached).
    p = _write_png(tmp_path / "code.png")
    for prompt in (f"image at `{p}`. go", f"see ({p}) here", f"the file {p}, then"):
        out = _user_content_with_images(prompt)
        assert isinstance(out, list), f"not attached: {prompt!r}"
        assert any(b.get("type") == "image_url" for b in out), prompt
