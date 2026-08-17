"""Tests for drydock.pdfredbox — the keystone: WHAT (query) → WHERE (real bbox) →
non-destructive box. Skips if the [pdf-redbox] extra (+ reportlab, dev) is absent."""
import pytest

pytest.importorskip("pdfplumber")
pytest.importorskip("pypdf")
reportlab = pytest.importorskip("reportlab")

from drydock import pdfredbox as rb


@pytest.fixture
def sample_pdf(tmp_path):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    p = tmp_path / "sample.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)   # 612 x 792
    c.drawString(100, 700, "Invoice #N00024-26-C-1234")
    c.drawString(100, 650, "Subtotal:       $1,247,392.00")
    c.drawString(100, 620, "Total Amount:   $1,247,392.00")   # repeated → needs context
    c.save()
    return str(p)


def test_search_finds_all_occurrences(sample_pdf):
    ms = rb.search(sample_pdf, "$1,247,392.00")
    assert len(ms) == 2                       # both Subtotal and Total rows
    assert all(m.page == 0 for m in ms)
    for m in ms:
        x0, top, x1, bottom = m.bbox
        assert x1 > x0 and bottom > top       # non-zero box, top-left origin


def test_context_disambiguates_repeated_value(sample_pdf):
    ms = rb.search(sample_pdf, "$1,247,392.00", context="Total Amount")
    assert len(ms) == 1                        # picked exactly one
    assert "Total Amount" in ms[0].context     # the right one (not Subtotal)
    assert "context" in ms[0].method


def test_redbox_is_non_destructive(sample_pdf, tmp_path):
    ms = rb.search(sample_pdf, "$1,247,392.00", context="Total Amount")
    out = str(tmp_path / "out.pdf")
    rec = rb.redbox_file(sample_pdf, ms, out)   # raises if text changed (firewall)
    assert len(rec["annotations"]) == 1
    # exactly one annotation was added, and page content text is byte-identical
    from pypdf import PdfReader
    r = PdfReader(out)
    annots = r.pages[0].get("/Annots")
    assert annots and len(annots) == 1


def test_coordinate_flip_lands_on_the_value(sample_pdf, tmp_path):
    # the boxed value's PDF-native rect must vertically bracket the drawString
    # baseline (y=620) we placed it at.
    ms = rb.search(sample_pdf, "$1,247,392.00", context="Total Amount")
    out = str(tmp_path / "out.pdf")
    rec = rb.redbox_file(sample_pdf, ms, out, pad=3)
    x0, y0, x1, y1 = rec["annotations"][0]["bbox_pdf"]
    assert y0 < 625 < y1 or y0 < 620 < y1      # brackets the row we drew at y≈620
    assert x1 > x0
