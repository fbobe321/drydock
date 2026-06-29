"""Document text extraction for GraphRAG ingestion — PDF + Word (.docx).

Kept clean-room and dependency-light:
  • .docx is a zip of XML, so we read it with STDLIB only (zipfile + ElementTree)
    — no dependency.
  • .pdf uses whatever the system already has: the `pdftotext` binary (poppler)
    if present, else the optional `pypdf` package (`pip install drydock-cli[pdf]`).

Returns None when a document can't be read (unsupported, corrupt, or no PDF
backend available) so ingestion skips it cleanly instead of failing.

All logic original to Drydock.
"""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# Extensions we can pull text out of (beyond the plain-text formats GraphRAG
# already reads directly). .ckl/.cklb are STIG checklists — we extract the
# meaningful per-rule text (id/severity/status/finding), not the raw XML/JSON.
EXTRACTABLE_EXT = {".pdf", ".docx", ".ckl", ".cklb"}

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _extract_docx(path: str | Path) -> str | None:
    """Pull paragraph text from a .docx (stdlib only). Each <w:p> is a paragraph;
    its text lives in <w:t> runs."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError):
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    paras: list[str] = []
    for p in root.iter(f"{_W}p"):
        runs = [t.text for t in p.iter(f"{_W}t") if t.text]
        if runs:
            paras.append("".join(runs))
    text = "\n\n".join(paras).strip()
    return text or None


def _extract_pdf(path: str | Path) -> str | None:
    """Extract text from a PDF: try the `pdftotext` binary first, then `pypdf`.
    Returns None if neither is available or the PDF has no extractable text."""
    # 1) pdftotext (poppler) — fast and common on Linux/macOS.
    try:
        r = subprocess.run(
            ["pdftotext", "-q", str(path), "-"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    # 2) pypdf (optional dependency: drydock-cli[pdf]).
    try:
        import pypdf

        reader = pypdf.PdfReader(str(path))
        text = "\n\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
        return text or None
    except Exception:  # noqa: BLE001 — optional dep missing / unreadable PDF
        return None


def _extract_checklist(path: str | Path) -> str | None:
    """Flatten a STIG .ckl/.cklb to readable, queryable text (per-rule id,
    severity, status, title, finding details) — so the agent can answer 'which
    findings are open?' over an ingested checklist."""
    try:
        from drydock import stig

        cl = stig.load(path)
    except Exception:  # noqa: BLE001 — unreadable/unsupported → skip cleanly
        return None
    host = cl.asset.get("HOST_NAME") or cl.asset.get("host_name") or "?"
    lines = [f"STIG Checklist: {cl.stig_name or 'STIG'} (host: {host})",
             "Counts: " + ", ".join(f"{k}={v}" for k, v in cl.counts().items())]
    for r in cl.rules:
        lines.append(f"\n{r.group_id} ({r.rule_id}) severity={r.severity} "
                     f"STATUS={r.status} — {r.title}")
        if r.finding_details:
            lines.append(f"  Finding: {r.finding_details}")
        if r.check_content:
            lines.append(f"  Check: {r.check_content[:300]}")
    return "\n".join(lines) or None


def extract_document(path: str | Path) -> str | None:
    """Extract text from a supported document (.pdf/.docx/.ckl/.cklb), or None."""
    ext = Path(path).suffix.lower()
    if ext == ".docx":
        return _extract_docx(path)
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext in (".ckl", ".cklb"):
        return _extract_checklist(path)
    return None


def pdf_backend_available() -> bool:
    """Whether a PDF can be extracted at all (pdftotext binary or pypdf)."""
    from shutil import which

    if which("pdftotext"):
        return True
    try:
        import pypdf  # noqa: F401

        return True
    except ImportError:
        return False
