"""PDF + Word (.docx) document extraction for GraphRAG ingestion."""
from __future__ import annotations

import zipfile

import pytest

from drydock import extract, graphrag


def _make_docx(path, text):
    doc = ('<?xml version="1.0"?><w:document xmlns:w='
           '"http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)


def _make_pdf(path, text):
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"]
    stream = b"BT /F1 18 Tf 72 700 Td (" + text.encode() + b") Tj ET"
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"; offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    x = len(out); out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for o in offs:
        out += ("%010d 00000 n \n" % o).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(x).encode() + b"\n%%EOF")
    path.write_bytes(out)


def test_docx_extraction(tmp_path):
    p = tmp_path / "d.docx"
    _make_docx(p, "The Nimbus service uses the Falcon-Token header.")
    assert extract.extract_document(p) == "The Nimbus service uses the Falcon-Token header."


def test_pdf_extraction(tmp_path):
    if not extract.pdf_backend_available():
        pytest.skip("no PDF backend (pdftotext/pypdf) available")
    p = tmp_path / "m.pdf"
    _make_pdf(p, "Refunds via the Zephyr gateway.")
    out = extract.extract_document(p)
    assert out and "Zephyr gateway" in out


def test_unsupported_and_corrupt_return_none(tmp_path):
    assert extract.extract_document(tmp_path / "x.png") is None
    bad = tmp_path / "bad.docx"; bad.write_text("not a zip")
    assert extract.extract_document(bad) is None


def test_graphrag_ingests_docx_and_pdf(tmp_path):
    _make_docx(tmp_path / "spec.docx", "The Nimbus API uses the Falcon-Token header for auth.")
    if extract.pdf_backend_available():
        _make_pdf(tmp_path / "manual.pdf", "Refunds are issued via the Zephyr gateway.")
    store = tmp_path / ".drydock" / "graphrag.json"
    graphrag.build_index(["."], store, cwd=str(tmp_path))
    idx = graphrag.load_index(store)
    srcs = graphrag.sources(idx)
    assert "spec.docx" in srcs
    assert graphrag.query_index(idx, "Falcon-Token header")["chunks"][0]["source"] == "spec.docx"
    if extract.pdf_backend_available():
        assert "manual.pdf" in srcs
        assert graphrag.query_index(idx, "refunds Zephyr gateway")["chunks"][0]["source"] == "manual.pdf"


def test_extract_ckl_checklist(tmp_path):
    p = tmp_path / "sys.ckl"
    p.write_text('<?xml version="1.0"?><CHECKLIST><ASSET><HOST_NAME>web01</HOST_NAME></ASSET>'
                 '<STIGS><iSTIG><STIG_INFO></STIG_INFO><VULN>'
                 '<STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>V-1</ATTRIBUTE_DATA></STIG_DATA>'
                 '<STIG_DATA><VULN_ATTRIBUTE>Rule_Title</VULN_ATTRIBUTE><ATTRIBUTE_DATA>disable debug</ATTRIBUTE_DATA></STIG_DATA>'
                 '<STATUS>Open</STATUS><FINDING_DETAILS>debug=true found</FINDING_DETAILS></VULN></iSTIG></STIGS></CHECKLIST>')
    out = extract.extract_document(p)
    assert out and "V-1" in out and "STATUS=open" in out and "debug=true found" in out


def test_graphrag_ingests_ckl(tmp_path):
    (tmp_path / "sys.ckl").write_text('<?xml version="1.0"?><CHECKLIST><STIGS><iSTIG><STIG_INFO></STIG_INFO>'
        '<VULN><STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>V-42</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>Rule_Title</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SSH root login disabled</ATTRIBUTE_DATA></STIG_DATA>'
        '<STATUS>NotAFinding</STATUS></VULN></iSTIG></STIGS></CHECKLIST>')
    store = graphrag.default_store_path(str(tmp_path))
    stats = graphrag.build_index(["."], store, cwd=str(tmp_path))
    assert stats["files"] == 1
    idx = graphrag.load_index(store)
    assert "sys.ckl" in graphrag.sources(idx)
    assert graphrag.query_index(idx, "SSH root login")["chunks"]
