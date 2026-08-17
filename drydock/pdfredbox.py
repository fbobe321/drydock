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


def _row_context(page, m, gap: float = 8.0) -> str:
    """Text of words on the same line to the LEFT of the match — the label that
    usually names the field (e.g. 'Total Amount:')."""
    words = page.extract_words()
    same_row = [w for w in words
                if abs(w["top"] - m["top"]) < gap and w["x1"] <= m["x0"] + 1]
    same_row.sort(key=lambda w: w["x0"])
    return " ".join(w["text"] for w in same_row)


def search(pdf_path: str, query: str, *, regex: bool = False,
           context: str | None = None) -> list[Match]:
    """Find `query` in the PDF text layer and return its true bounding box(es).
    If `context` is given and several occurrences exist, keep the one whose row
    label matches the context (PRD §7 L3)."""
    import pdfplumber  # pyright: ignore[reportMissingImports]

    pat = query if regex else re.escape(query)
    out: list[Match] = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages):
            for h in page.search(pat, regex=True, case=False):
                out.append(Match(
                    page=pno,
                    bbox=(h["x0"], h["top"], h["x1"], h["bottom"]),
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
        drawn.append({"page": m.page, "value": m.text, "bbox_pdf": [round(v, 2) for v in rect],
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
