"""Drydock PDF Redboxing — keystone engine.

Design constraint (Redboxing PRD §3/§33): the LLM decides WHAT text to box; this
module deterministically decides WHERE and draws it. It NEVER asks a model for
coordinates.

Backend: permissive only (no AGPL) — pdfplumber (MIT) for word bounding boxes,
pypdf (BSD) for a non-destructive rectangle *annotation*. Rendering for optional
vision-verification uses pypdfium2 (Apache/BSD). Deps are an optional extra
(`drydock-cli[pdf-redbox]`), imported lazily.

FIREWALL (PRD §12/§27): redbox is purely ADDITIVE. It overlays an annotation on a
COPY and never mutates page content — a separate write path from the doc-canvas
`redact()` (which is text mutation). `redbox_file` asserts the source text is
byte-for-byte unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RED = (1.0, 0.0, 0.0)


def _norm(s: str) -> str:
    """Whitespace/punctuation-insensitive form for matching (PRD §7 L2)."""
    s = s.replace(" ", " ").replace(" ", " ")   # thin/nbsp → space
    return re.sub(r"\s+", "", s)


@dataclass
class Match:
    page: int                       # 0-indexed page number
    bbox: tuple                     # (x0, top, x1, bottom) — TOP-LEFT origin, PDF points
    text: str
    context: str = ""               # words to the left / above (for disambiguation)
    method: str = "exact"
    confidence: float = 0.0
    field: str = ""                 # the semantic field this box answers (for audit)


def _row_context(page, m, gap: float = 8.0) -> str:
    """Text of words on the same line to the LEFT of the match — the label that
    usually names the field (e.g. 'Total Amount:')."""
    words = page.extract_words()
    same_row = [w for w in words
                if abs(w["top"] - m["top"]) < gap and w["x1"] <= m["x0"] + 1]
    same_row.sort(key=lambda w: w["x0"])
    return " ".join(w["text"] for w in same_row)


def _snap_to_words(page, bbox, gap: float = 6.0):
    """Expand a bbox to the full extent of the words it overlaps on the same row —
    so the box tracks WHOLE tokens even when the query matched a substring (e.g. the
    LLM dropped a leading '#'). Keeps boxes tight but complete."""
    x0, top, x1, bottom = bbox
    cy = (top + bottom) / 2
    for w in page.extract_words():
        if abs((w["top"] + w["bottom"]) / 2 - cy) > gap:
            continue                                   # different row
        if w["x1"] > x0 and w["x0"] < x1:              # horizontally overlaps the match
            x0 = min(x0, w["x0"]); x1 = max(x1, w["x1"])
            top = min(top, w["top"]); bottom = max(bottom, w["bottom"])
    return (x0, top, x1, bottom)


def search(pdf_path: str, query: str, *, regex: bool = False,
           context: str | None = None, snap: bool = True) -> list[Match]:
    """Find `query` in the PDF text layer and return its true bounding box(es).
    If `context` is given and several occurrences exist, keep the one whose row
    label matches the context (PRD §7 L3). `snap` expands each box to whole tokens."""
    import pdfplumber  # pyright: ignore[reportMissingImports]

    pat = query if regex else re.escape(query)
    out: list[Match] = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages):
            for h in page.search(pat, regex=True, case=False):
                raw = (h["x0"], h["top"], h["x1"], h["bottom"])
                out.append(Match(
                    page=pno,
                    bbox=_snap_to_words(page, raw) if snap else raw,
                    text=h["text"],
                    context=_row_context(page, h),
                    method="exact" if not regex else "regex",
                    confidence=0.99,
                ))
    if context and len(out) > 1:
        nctx = _norm(context).lower()
        preferred = [m for m in out if nctx in _norm(m.context).lower()]
        if preferred:
            for m in preferred:
                m.method += "+context"
                m.confidence = 0.95
            return preferred
    if len(out) == 1:
        out[0].confidence = 0.99
    elif len(out) > 1:
        for m in out:
            m.confidence = 0.6              # ambiguous → flag for review (PRD §15)
    return out


def _to_pdf_rect(bbox, page_height: float, pad: float):
    """pdfplumber TOP-LEFT (x0, top, x1, bottom) → pypdf PDF-native BOTTOM-LEFT
    (x0, y0, x1, y1). PDF y grows UP, so flip about the page height (PRD §21)."""
    x0, top, x1, bottom = bbox
    return (x0 - pad, page_height - bottom - pad, x1 + pad, page_height - top + pad)


def redbox_file(pdf_path: str, matches: list[Match], out_path: str,
                *, pad: float = 3.0, color=RED) -> dict:
    """Draw a non-destructive red rectangle annotation for each match onto a COPY.
    Returns an audit record (PRD §26). Asserts source text is unchanged."""
    import pdfplumber  # pyright: ignore[reportMissingImports]
    from pypdf import PdfReader, PdfWriter  # pyright: ignore[reportMissingImports]
    from pypdf.annotations import Rectangle  # pyright: ignore[reportMissingImports]
    from pypdf.generic import ArrayObject, FloatObject, NameObject  # pyright: ignore[reportMissingImports]

    with pdfplumber.open(pdf_path) as pdf:
        heights = [p.height for p in pdf.pages]

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.append(reader)

    drawn = []
    for m in matches:
        rect = _to_pdf_rect(m.bbox, heights[m.page], pad)
        annot = Rectangle(rect=rect)
        # Red STROKE, no interior fill → the content stays fully visible (a box,
        # not a redaction). /C is the annotation's colour array.
        annot[NameObject("/C")] = ArrayObject([FloatObject(c) for c in color])
        writer.add_annotation(page_number=m.page, annotation=annot)
        drawn.append({"field": m.field, "page": m.page, "value": m.text,
                      "bbox_pdf": [round(v, 2) for v in rect],
                      "method": m.method, "confidence": m.confidence})

    with open(out_path, "wb") as f:
        writer.write(f)

    # FIREWALL check: content text must be byte-identical (we only added overlays)
    _assert_text_unchanged(pdf_path, out_path)
    return {"source": pdf_path, "output": out_path, "annotations": drawn}


def _assert_text_unchanged(src: str, out: str) -> None:
    import pdfplumber  # pyright: ignore[reportMissingImports]
    def alltext(p):
        with pdfplumber.open(p) as pdf:
            return "␟".join((pg.extract_text() or "") for pg in pdf.pages)
    if alltext(src) != alltext(out):
        raise RuntimeError("redbox altered document text — refusing (redbox must be "
                           "additive; this is not redaction)")


# ── Semantic layer (PRD §8/§9): field name → VERBATIM on-page value → search ──

_FIND_SYSTEM = (
    "You locate specific fields in a document so they can be boxed for review. "
    "For EACH requested field, find the value AS IT LITERALLY APPEARS in the "
    "document and copy it CHARACTER-FOR-CHARACTER — same digits, punctuation, "
    "spacing, and casing — so it can be found on the page. Do NOT reformat, "
    "normalize, compute, or summarize; copy the exact on-page substring. Also give "
    "the 1-based page number and a short nearby label ('context') that "
    "distinguishes it from similar values. If a field is absent, set found=false."
)


def extract_pages_text(pdf_path: str) -> str:
    """Document text with [PAGE n] markers (1-based) so the LLM can cite pages."""
    import pdfplumber  # pyright: ignore[reportMissingImports]
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            parts.append(f"[PAGE {i}]\n{page.extract_text() or ''}")
    return "\n\n".join(parts)


def build_find_prompt(fields: list[str], doc_text: str) -> str:
    flist = "\n".join(f"- {f}" for f in fields)
    return (
        f"{_FIND_SYSTEM}\n\nRequested fields:\n{flist}\n\nDocument:\n{doc_text}\n\n"
        'Return ONLY a JSON array, one object per requested field:\n'
        '[{"field": "<the requested field>", "value": "<verbatim on-page text>", '
        '"page": <int>, "context": "<short nearby label>", "found": <true|false>}]'
    )


def parse_fields(raw: str) -> list[dict]:
    """Parse the LLM's JSON array, tolerating code fences / prose around it."""
    import json
    s = raw.strip()
    if "```" in s:
        s = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", s.strip())
    a, b = s.find("["), s.rfind("]")
    if a != -1 and b != -1:
        s = s[a:b + 1]
    try:
        data = json.loads(s)
    except Exception:  # noqa: BLE001
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def find_fields(pdf_path: str, fields: list[str], *, llm) -> list[dict]:
    """LLM identifies each field's verbatim on-page value in ONE pass (PRD §9).
    `llm` is a callable(prompt:str) -> str (injected → testable without a model)."""
    return parse_fields(llm(build_find_prompt(fields, extract_pages_text(pdf_path))))


def redbox_semantic(pdf_path: str, fields: list[str], out_path: str, *, llm,
                    pad: float = 3.0, vision_llm=None) -> dict:
    """Full semantic pipeline: LLM finds each field's verbatim value → deterministic
    search locates it → non-destructive box. Returns the audit record + a per-field
    resolution map (boxed / flagged / not-found) for review (PRD §15/§26). If
    `vision_llm` is given, each drawn box is visually verified (PRD §14) and the
    verdict drives the final status/confidence — an unverified box is flagged."""
    matches: list[Match] = []
    resolved = []
    match_res = []                                     # resolved entry per match (parallel)
    for f in find_fields(pdf_path, fields, llm=llm):
        name = str(f.get("field", ""))
        if not f.get("found") or not f.get("value"):
            resolved.append({"field": name, "status": "not_found"})
            continue
        ms = search(pdf_path, str(f["value"]), context=f.get("context"))
        page = f.get("page")
        if page and len(ms) > 1:                       # narrow to the stated page
            on_page = [m for m in ms if m.page == int(page) - 1]
            if on_page:
                ms = on_page
        if not ms:
            resolved.append({"field": name, "value": f.get("value"),
                             "status": "value_not_on_page"})
            continue
        m = ms[0]
        m.field = name
        if len(ms) > 1 and "context" not in m.method:
            status = "ambiguous_flagged"
        else:
            status = "boxed" if m.confidence >= 0.9 else "boxed_flagged"
        entry = {"field": name, "value": m.text, "page": m.page + 1,
                 "confidence": m.confidence, "status": status}
        matches.append(m); resolved.append(entry); match_res.append(entry)
    audit = (redbox_file(pdf_path, matches, out_path, pad=pad) if matches
             else {"source": pdf_path, "output": None, "annotations": []})

    # PRD §14: visually verify each drawn box; the verdict drives the final status.
    if matches and vision_llm is not None:
        verdicts = verify_boxes(out_path, matches, vision_llm)
        for v, ann, entry in zip(verdicts, audit["annotations"], match_res):
            ann["verified"] = v["verified"]; ann["vision_confidence"] = v["confidence"]
            entry["verified"] = v["verified"]; entry["vision_confidence"] = v["confidence"]
            if v["verified"] is True:
                entry["status"] = "boxed_verified"
            elif v["verified"] is False:
                entry["status"] = "boxed_UNVERIFIED"       # drawn, but vision disagrees → review

    audit["fields"] = resolved
    return audit


def make_llm(config: dict):
    """Adapter: drydock's configured provider → a one-shot llm(prompt)->str for the
    semantic layer. Any chat model works; the heavy coding model isn't required."""
    from openai import OpenAI  # pyright: ignore[reportMissingImports]
    from drydock.providers import PROVIDERS
    provider = config.get("provider", "vllm")
    base = config.get("base_url") or PROVIDERS.get(provider, {}).get("base_url")
    client = OpenAI(base_url=base, api_key=config.get("api_key") or "dummy")
    model = config.get("model", "gemma4")

    def llm(prompt: str) -> str:
        r = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content or ""
    return llm


# ── Vision verification (PRD §14): render the box → a vision model confirms it ─

def render_region(pdf_path: str, page: int, bbox, out_png: str, *,
                  scale: float = 2.0, margin: float = 24.0) -> str:
    """Render `page` and crop to `bbox` (+margin) as a PNG. bbox is TOP-LEFT PDF
    points; the render is top-left-origin scaled pixels."""
    import pypdfium2 as pdfium  # pyright: ignore[reportMissingImports]
    doc = pdfium.PdfDocument(pdf_path)
    # pypdfium2 render() takes a float scale multiplier at runtime; its stub types
    # it int — ignore the false positive rather than lose sub-integer scaling.
    pil = doc[page].render(scale=scale).to_pil()  # pyright: ignore[reportArgumentType]
    x0, top, x1, bottom = bbox
    crop = (max(0, (x0 - margin) * scale), max(0, (top - margin) * scale),
            min(pil.width, (x1 + margin) * scale), min(pil.height, (bottom + margin) * scale))
    pil.crop(crop).save(out_png)
    return out_png


def make_vision_llm(config: dict):
    """Adapter: drydock's provider → vlm(prompt, image_path) -> str, for §14
    verification. Needs a vision-capable model (e.g. gemma-4 with its mmproj)."""
    import base64
    from openai import OpenAI  # pyright: ignore[reportMissingImports]
    from drydock.providers import PROVIDERS
    provider = config.get("provider", "vllm")
    base = config.get("base_url") or PROVIDERS.get(provider, {}).get("base_url")
    client = OpenAI(base_url=base, api_key=config.get("api_key") or "dummy")
    model = config.get("model", "gemma4")

    def vlm(prompt: str, image_path: str) -> str:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = client.chat.completions.create(model=model, temperature=0, messages=[
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}])
        return r.choices[0].message.content or ""
    return vlm


def _parse_verdict(raw: str) -> dict:
    import json
    s = raw.strip()
    if "```" in s:
        s = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", s.strip())
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b != -1:
        s = s[a:b + 1]
    try:
        d = json.loads(s)
        return {"verified": bool(d.get("verified")),
                "confidence": float(d.get("confidence", 0.0)),
                "note": str(d.get("note", ""))[:200]}
    except Exception:  # noqa: BLE001
        return {"verified": None, "confidence": 0.0, "note": "unparseable verdict"}


def verify_boxes(boxed_pdf_path: str, matches: list[Match], vision_llm) -> list[dict]:
    """Render each drawn box (from the ALREADY-BOXED pdf, so the red rectangle is
    visible) and ask a vision model to confirm it surrounds the intended value
    (PRD §14). Returns one verdict per match: {verified, confidence, note}.
    `vision_llm` is a callable(prompt, image_path)->str. Never raises."""
    import os
    import tempfile
    verdicts = []
    with tempfile.TemporaryDirectory() as d:
        for i, m in enumerate(matches):
            try:
                png = render_region(boxed_pdf_path, m.page, m.bbox, os.path.join(d, f"v{i}.png"))
                what = f"the {m.field}" if m.field else f'the text "{m.text}"'
                prompt = (
                    "This is a cropped region of a document with a RED rectangle drawn on "
                    f'it. Does the red rectangle surround {what} (value: "{m.text}")? '
                    'Reply ONLY JSON: {"verified": true|false, "confidence": 0.0-1.0, '
                    '"note": "<brief>"}')
                verdicts.append(_parse_verdict(vision_llm(prompt, png)))
            except Exception as e:  # noqa: BLE001 — verification must never break the box
                verdicts.append({"verified": None, "confidence": 0.0, "note": f"verify error: {e}"})
    return verdicts
