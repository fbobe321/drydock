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


# ── semantic layer (LLM injected as a stub → testable without a live model) ──

def _stub_llm(payload):
    import json
    def llm(prompt):
        assert "CHARACTER-FOR-CHARACTER" in prompt and "JSON array" in prompt
        return json.dumps(payload)
    return llm


def test_find_fields_returns_verbatim_values(sample_pdf):
    llm = _stub_llm([
        {"field": "total contract value", "value": "$1,247,392.00", "page": 1,
         "context": "Total Amount", "found": True},
        {"field": "missing", "value": "", "page": 0, "context": "", "found": False},
    ])
    fields = rb.find_fields(sample_pdf, ["total contract value", "missing"], llm=llm)
    assert len(fields) == 2 and fields[0]["value"] == "$1,247,392.00"


def test_redbox_semantic_end_to_end(sample_pdf, tmp_path):
    llm = _stub_llm([
        {"field": "total contract value", "value": "$1,247,392.00", "page": 1,
         "context": "Total Amount", "found": True},
        {"field": "contract number", "value": "#N00024-26-C-1234", "page": 1,
         "context": "Invoice", "found": True},
        {"field": "signature", "value": "", "page": 0, "context": "", "found": False},
    ])
    out = str(tmp_path / "sem.pdf")
    audit = rb.redbox_semantic(sample_pdf, ["total contract value", "contract number",
                                            "signature"], out, llm=llm)
    byfield = {f["field"]: f for f in audit["fields"]}
    assert byfield["total contract value"]["status"].startswith("boxed")
    assert byfield["contract number"]["status"].startswith("boxed")
    assert byfield["signature"]["status"] == "not_found"     # LLM said absent
    assert len(audit["annotations"]) == 2                    # two boxes drawn
    from pypdf import PdfReader
    assert len(PdfReader(out).pages[0]["/Annots"]) == 2


def test_semantic_flags_value_not_on_page(sample_pdf, tmp_path):
    # LLM hallucinates a value that isn't in the document → flagged, not boxed
    llm = _stub_llm([{"field": "total", "value": "$9,999,999.99", "page": 1,
                      "context": "Total", "found": True}])
    audit = rb.redbox_semantic(sample_pdf, ["total"], str(tmp_path / "o.pdf"), llm=llm)
    assert audit["fields"][0]["status"] == "value_not_on_page"
    assert audit["annotations"] == []


def test_parse_fields_tolerates_code_fence():
    raw = '```json\n[{"field":"x","value":"1","page":1,"context":"","found":true}]\n```'
    got = rb.parse_fields(raw)
    assert got and got[0]["value"] == "1"


def test_pdf_tools_registered_and_surface_without_flags():
    """The harness knows it can redbox: the tools are registered and surface for a
    natural-language PDF request — no switches/steps for the user."""
    import drydock.tools  # noqa: F401  — triggers register_all()
    from drydock.tool_registry import schemas
    from drydock.tool_select import select_tools
    names = {s["name"] for s in schemas()}
    assert {"PdfRedbox", "PdfSearch"} <= names
    surf = {s["name"] for s in select_tools(schemas(),
            task_text="redbox the total and contract number in contract.pdf", max_tools=12)}
    assert "PdfRedbox" in surf and "PdfSearch" in surf
    off = {s["name"] for s in select_tools(schemas(),
           task_text="refactor the auth module", max_tools=12)}
    assert "PdfRedbox" not in off        # not surfaced for unrelated work


# ── verbatim-snap + vision verification ─────────────────────────────────────

def _stub_vision(verdict):
    import json
    def vlm(prompt, image_path):
        assert prompt and image_path            # got a real rendered crop path
        return json.dumps(verdict)
    return vlm


def test_snap_expands_box_to_full_token(sample_pdf):
    # searching a substring (no leading '#') should still box the whole token
    raw = rb.search(sample_pdf, "N00024-26-C-1234", snap=False)[0]
    snapped = rb.search(sample_pdf, "N00024-26-C-1234", snap=True)[0]
    assert snapped.bbox[0] <= raw.bbox[0]       # extended LEFT to include the '#'


def test_vision_verify_sets_verified_status(sample_pdf, tmp_path):
    pytest.importorskip("pypdfium2")
    llm = _stub_llm([{"field": "total contract value", "value": "$1,247,392.00",
                      "page": 1, "context": "Total Amount", "found": True}])
    vlm = _stub_vision({"verified": True, "confidence": 0.96, "note": "tight box"})
    audit = rb.redbox_semantic(sample_pdf, ["total contract value"],
                               str(tmp_path / "v.pdf"), llm=llm, vision_llm=vlm)
    f = audit["fields"][0]
    assert f["status"] == "boxed_verified" and f["vision_confidence"] == 0.96
    assert audit["annotations"][0]["verified"] is True


def test_vision_verify_flags_unverified(sample_pdf, tmp_path):
    pytest.importorskip("pypdfium2")
    llm = _stub_llm([{"field": "total", "value": "$1,247,392.00", "page": 1,
                      "context": "Total Amount", "found": True}])
    vlm = _stub_vision({"verified": False, "confidence": 0.2, "note": "box misses it"})
    audit = rb.redbox_semantic(sample_pdf, ["total"], str(tmp_path / "v.pdf"),
                               llm=llm, vision_llm=vlm)
    assert audit["fields"][0]["status"] == "boxed_UNVERIFIED"
