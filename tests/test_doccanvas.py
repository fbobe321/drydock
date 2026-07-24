"""Tests for the Document Canvas engine (drydock/doccanvas.py). Stdlib only."""
import pytest

from drydock import doccanvas as dc

MD = """# Security Plan

## 4.2 Authentication

The system shall authenticate users with single-factor authentication.

| Method | Requirement |
| Password | Required |

- item one
- item two

```python
x = 1
```

## 4.3 Logging

Logs shall be retained.
"""


def _doc(tmp_path, text=MD, name="doc.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_block_types(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    types = [b.type for b in doc.blocks]
    assert "heading" in types and "paragraph" in types
    assert "table" in types and "list" in types and "code" in types
    # ids are typed + stable-formatted
    assert any(b.id.startswith("sec-") for b in doc.blocks)
    assert any(b.id.startswith("para-") for b in doc.blocks)
    # every block has a content hash
    assert all(b.content_hash for b in doc.blocks)


def test_outline_and_search(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    ol = dc.outline(doc)
    titles = [o["title"] for o in ol]
    assert "Security Plan" in titles
    assert any("Authentication" in t for t in titles)
    hits = dc.search(doc, "single-factor")
    assert len(hits) == 1 and hits[0]["id"].startswith("para-")


def test_read_neighbors(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    target = dc.search(doc, "single-factor")[0]["id"]
    r = dc.read(doc, target, before=1, after=1)
    assert r["target"] == target
    assert any(w["target"] for w in r["window"])
    assert len(r["window"]) >= 2


def test_replace_patch_with_hash_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    b = doc.get(dc.search(doc, "single-factor")[0]["id"])
    log = dc.apply_patches(doc, [{"op": "replace", "target_id": b.id,
                                  "expected_hash": b.content_hash,
                                  "new_text": "The system shall require phishing-resistant MFA.",
                                  "reason": "policy update"}])
    assert "MFA" in doc.get(b.id).text
    assert log[0]["op"] == "replace"
    # hash was refreshed after edit
    assert doc.get(b.id).content_hash == dc._hash(doc.get(b.id).text)


def test_stale_hash_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    b = doc.get(dc.search(doc, "single-factor")[0]["id"])
    with pytest.raises(dc.PatchError, match="stale"):
        dc.apply_patches(doc, [{"op": "replace", "target_id": b.id,
                                "expected_hash": "deadbeef0000",
                                "new_text": "nope"}])
    # unchanged
    assert "single-factor" in doc.get(b.id).text


def test_atomic_failure_leaves_doc_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    good = doc.get(dc.search(doc, "single-factor")[0]["id"])
    before = good.text
    # first patch valid, second references a bad id -> whole batch rejected
    with pytest.raises(dc.PatchError):
        dc.apply_patches(doc, [
            {"op": "replace", "target_id": good.id, "new_text": "changed"},
            {"op": "replace", "target_id": "para-9999", "new_text": "x"},
        ])
    assert doc.get(good.id).text == before


def test_insert_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    anchor = dc.search(doc, "single-factor")[0]["id"]
    n0 = len(doc.blocks)
    dc.apply_patches(doc, [{"op": "insert_after", "target_id": anchor,
                            "new_text": "NEW paragraph.", "reason": "add"}])
    assert len(doc.blocks) == n0 + 1
    dc.apply_patches(doc, [{"op": "delete", "target_id": anchor}])
    assert len(doc.blocks) == n0
    assert doc.get(anchor) is None


def test_validate_required_prohibited(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    res = dc.validate(doc, required=["Logging"], prohibited=["single-factor"])
    checks = {f["check"]: f["ok"] for f in res["findings"]}
    assert checks["required:'Logging'"] is True
    assert checks["prohibited:'single-factor'"] is False   # still present
    assert checks["unique_ids"] and checks["hashes_consistent"]


def test_commit_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    src = _doc(tmp_path)
    doc = dc.open_document(str(src))
    b = doc.get(dc.search(doc, "single-factor")[0]["id"])
    dc.apply_patches(doc, [{"op": "replace", "target_id": b.id,
                            "expected_hash": b.content_hash,
                            "new_text": "The system shall require MFA."}])
    info = dc.commit(doc)
    assert (tmp_path / "doc.md.orig").exists()
    assert "single-factor" in (tmp_path / "doc.md.orig").read_text()  # original intact
    assert "MFA" in src.read_text() and "single-factor" not in src.read_text()
    assert info["blocks"] == len(doc.blocks)


def test_persistence_roundtrip_preserves_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    src = _doc(tmp_path)
    doc = dc.open_document(str(src))
    ids = [b.id for b in doc.blocks]
    # reopen without reparse -> same ids from the store
    doc2 = dc.open_document(str(src))
    assert [b.id for b in doc2.blocks] == ids


def test_multi_patch_atomic_success(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    a = dc.search(doc, "single-factor")[0]["id"]
    b = dc.search(doc, "retained")[0]["id"]
    log = dc.apply_patches(doc, [
        {"op": "replace", "target_id": a, "new_text": "Use MFA."},
        {"op": "insert_after", "target_id": b, "new_text": "Extra note."},
    ])
    assert len(log) == 2
    assert "MFA" in doc.get(a).text
    assert any(bl.text == "Extra note." for bl in doc.blocks)


def test_insert_before_ordering(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    anchor = dc.search(doc, "single-factor")[0]["id"]
    dc.apply_patches(doc, [{"op": "insert_before", "target_id": anchor,
                            "new_text": "PRELUDE."}])
    ai = doc.index_of(anchor)
    assert doc.blocks[ai - 1].text == "PRELUDE."


def test_render_roundtrip_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    doc = dc.open_document(str(_doc(tmp_path)))
    once = dc.render(doc)
    doc2 = dc.parse(once, "markdown", "x.md")
    assert dc.render(doc2) == once


def test_unicode_hash_and_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    p = _doc(tmp_path, "# Café ☕\n\nRésumé naïve façade — 日本語.\n", "u.md")
    doc = dc.open_document(str(p))
    b = dc.search(doc, "Résumé")[0]["id"]
    assert doc.get(b).content_hash == dc._hash(doc.get(b).text)
    dc.apply_patches(doc, [{"op": "replace", "target_id": b,
                            "expected_hash": doc.get(b).content_hash,
                            "new_text": "Updated — Ω 漢字."}])
    assert "漢字" in doc.get(b).text


def test_plaintext_paragraphs(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    p = _doc(tmp_path, "Alpha para.\n\nBeta para.\n\nGamma para.\n", "notes.txt")
    doc = dc.open_document(str(p))
    assert doc.fmt == "text"
    assert [b.type for b in doc.blocks] == ["paragraph"] * 3


def test_docx_import_to_sidecar(tmp_path, monkeypatch):
    import zipfile
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    docx = tmp_path / "policy.docx"
    ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
          'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.wordprocessingml.document.main+xml"/></Types>')
    body = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body>')
    for t in ["Access Control Policy", "Use single-factor authentication.", "Logging required."]:
        body += f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>"
    body += "</w:body></w:document>"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("word/document.xml", body)
    doc = dc.open_document(str(docx))
    assert doc.source_path.endswith("policy.docx.canvas.md")   # sidecar, not the binary
    assert doc.meta.get("imported_from", "").endswith("policy.docx")
    assert len(doc.blocks) == 3
    assert dc.search(doc, "single-factor")


def test_replace_all_case_sensitivity(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    p = _doc(tmp_path, "Napoleon and NAPOLEON and napoleon.\n", "n.txt")
    doc = dc.open_document(str(p))
    dc.replace_all(doc, "Napoleon", "X")                    # case-sensitive default
    assert doc.blocks[0].text == "X and NAPOLEON and napoleon."
    dc.replace_all(doc, "napoleon", "Y", ignore_case=True)  # all casings
    assert "NAPOLEON" not in doc.blocks[0].text and "napoleon" not in doc.blocks[0].text


def test_redact_verified_and_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "STORE_DIR", tmp_path / "store")
    p = _doc(tmp_path, "Agent SECRET-7788 met SECRET-7788 twice.\n\nClean para.\n", "s.txt")
    doc = dc.open_document(str(p))
    r = dc.redact(doc, query="SECRET-7788")
    assert r["verified"] and r["redacted"] == 2
    assert "SECRET-7788" not in dc.render(doc)          # non-recoverable
    assert doc.redactions and doc.redactions[0]["count"] == 2
    # marker-contains-query is refused
    import pytest as _pt
    with _pt.raises(dc.PatchError):
        dc.redact(doc, query="RED", marker="[REDACTED]")
