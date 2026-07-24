"""Document Canvas — random-access, structure-aware editing of large documents.

Design (clean-room, stdlib only — no third-party deps, no embeddings). The
model treats a big document like a codebase: get an outline, search, open a
small region, apply a HASH-GUARDED patch inside a transaction, validate, and
commit. It never holds the whole document.

Core ideas
  • A source file (Markdown or plain text for this MVP) is parsed once into a
    CANONICAL MODEL: an ordered list of addressable BLOCKS (heading, paragraph,
    code, table, list item, blank). Every block gets a PERMANENT id
    (``sec-0001``/``para-0004``/…) and a ``content_hash`` (sha256 prefix).
  • The model is persisted as JSON under ``~/.drydock/doccanvas/<name>.json`` so
    ids and hashes survive across turns and edits. Page numbers are navigation
    hints only; ids are the stable address.
  • Edits are STRUCTURED PATCHES against ids, guarded by ``expected_hash`` so the
    model can never edit stale content. Patches run in a TRANSACTION: stage →
    validate → preview diff → commit or rollback. Commit re-renders the blocks
    back to the source format and writes it, preserving the original as an
    immutable ``.orig`` artifact on first commit.

This module is pure logic (no TUI, no tool wiring); ``drydock/tools`` exposes it
as Doc* tools and the audit rides on ``drydock.events``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

STORE_DIR = Path.home() / ".drydock" / "doccanvas"

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+\S")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


@dataclass
class Block:
    """One addressable document object."""
    id: str
    type: str                      # heading|paragraph|code|table|list|blank
    text: str
    content_hash: str = ""
    level: int = 0                 # heading depth (1-6); 0 otherwise
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = _hash(self.text)

    def rehash(self) -> None:
        self.content_hash = _hash(self.text)


@dataclass
class Document:
    """Canonical model: source pointer + ordered blocks + id counters."""
    source_path: str
    fmt: str                       # markdown|text
    blocks: list = field(default_factory=list)   # list[Block]
    counters: dict = field(default_factory=dict)  # type -> last integer used
    source_hash: str = ""          # hash of the source file at parse time
    committed: bool = False        # has an .orig artifact been written yet
    redactions: list = field(default_factory=list)  # audit of redactions applied
    meta: dict = field(default_factory=dict)  # e.g. imported_from for pdf/docx

    # ---- id allocation -------------------------------------------------
    _PREFIX = {"heading": "sec", "paragraph": "para", "code": "code",
               "table": "table", "list": "list", "blank": "blank"}

    def _new_id(self, btype: str) -> str:
        n = self.counters.get(btype, 0) + 1
        self.counters[btype] = n
        return f"{self._PREFIX.get(btype, 'blk')}-{n:04d}"

    # ---- lookups -------------------------------------------------------
    def index_of(self, block_id: str) -> int:
        for i, b in enumerate(self.blocks):
            if b.id == block_id:
                return i
        return -1

    def get(self, block_id: str):
        i = self.index_of(block_id)
        return self.blocks[i] if i >= 0 else None


# ---------------------------------------------------------------------------
# Parsing (source -> canonical model)
# ---------------------------------------------------------------------------
def _classify_para(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and all(_TABLE_ROW.match(ln) for ln in lines):
        return "table"
    if lines and all(_LIST_ITEM.match(ln) for ln in lines):
        return "list"
    return "paragraph"


def parse(text: str, fmt: str, source_path: str) -> Document:
    """Parse raw text into a Document. Markdown recognises headings, fenced
    code, tables and lists; plain text splits on blank lines only."""
    doc = Document(source_path=source_path, fmt=fmt, source_hash=_hash(text))
    lines = text.split("\n")
    i, n = 0, len(lines)
    buf: list[str] = []

    def flush_para():
        if not buf:
            return
        raw = "\n".join(buf).strip("\n")
        buf.clear()
        if not raw.strip():
            return
        btype = _classify_para(raw) if fmt == "markdown" else "paragraph"
        doc.blocks.append(Block(id=doc._new_id(btype), type=btype, text=raw))

    while i < n:
        line = lines[i]
        if fmt == "markdown":
            m = _HEADING.match(line)
            if m:
                flush_para()
                doc.blocks.append(Block(id=doc._new_id("heading"), type="heading",
                                        text=line.rstrip(), level=len(m.group(1))))
                i += 1
                continue
            fm = _FENCE.match(line)
            if fm:
                flush_para()
                fence = line.strip()[:3]
                code = [line]
                i += 1
                while i < n:
                    code.append(lines[i])
                    if lines[i].strip().startswith(fence):
                        i += 1
                        break
                    i += 1
                doc.blocks.append(Block(id=doc._new_id("code"), type="code",
                                        text="\n".join(code)))
                continue
        if not line.strip():
            flush_para()
        else:
            buf.append(line)
        i += 1
    flush_para()
    return doc


# ---------------------------------------------------------------------------
# Rendering (canonical model -> source text)
# ---------------------------------------------------------------------------
def render(doc: Document) -> str:
    """Serialise blocks back to source text. Blocks are separated by a blank
    line (the natural Markdown/prose separator); code and headings render as-is."""
    return "\n\n".join(b.text for b in doc.blocks) + "\n"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _store_path(name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return STORE_DIR / f"{safe}.json"


def store_name(source_path: str) -> str:
    return Path(source_path).name


def save(doc: Document) -> Path:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    p = _store_path(store_name(doc.source_path))
    data = asdict(doc)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(name: str) -> Document | None:
    p = _store_path(name)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    blocks = [Block(**b) for b in d.pop("blocks", [])]
    doc = Document(**d)
    doc.blocks = blocks
    return doc


_IMPORT_EXT = (".pdf", ".docx")


def open_document(source_path: str, reparse: bool = False) -> Document:
    """Open a source file into a canonical model. Reuses a persisted store if
    the source is unchanged (preserving ids); reparses on change or reparse=1.

    .md/.markdown/.txt are edited IN PLACE. .pdf/.docx are IMPORTED read-only
    (text extracted via drydock.extract): edits go to a Markdown sidecar
    ``<source>.canvas.md`` and the binary original is never overwritten."""
    path = Path(source_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(source_path)
    ext = path.suffix.lower()

    if ext in _IMPORT_EXT:
        from drydock import extract
        if ext == ".pdf" and not extract.pdf_backend_available():
            raise RuntimeError(
                "PDF import needs a backend: install `pdftotext` (poppler) or "
                "`pip install drydock-cli[pdf]`.")
        text = extract.extract_document(path)
        if not text:
            raise RuntimeError(f"could not extract any text from {path.name}")
        edit_path = path.with_name(path.name + ".canvas.md")
        name = edit_path.name
        existing = load(name)
        if existing and not reparse and existing.source_hash == _hash(text):
            return existing
        doc = parse(text, "markdown", str(edit_path))
        doc.meta = {"imported_from": str(path), "import_ext": ext}
        save(doc)
        return doc

    text = path.read_text(encoding="utf-8", errors="replace")
    fmt = "markdown" if ext in (".md", ".markdown") else "text"
    existing = load(path.name)
    if existing and not reparse and existing.source_hash == _hash(text):
        return existing
    doc = parse(text, fmt, str(path))
    save(doc)
    return doc


# ---------------------------------------------------------------------------
# Read / navigate
# ---------------------------------------------------------------------------
def outline(doc: Document) -> list:
    """Heading hierarchy with the id and a running count of child blocks."""
    out = []
    for i, b in enumerate(doc.blocks):
        if b.type == "heading":
            title = _HEADING.match(b.text)
            out.append({"id": b.id, "level": b.level,
                        "title": title.group(2) if title else b.text})
    return out


def search(doc: Document, query: str, regex: bool = False,
           max_hits: int = 100, ignore_case: bool = True) -> list:
    """Lexical search over block text. Returns id/type/snippet per hit.
    Case-insensitive by default (find loosely); pass ignore_case=False to match
    exactly — the same casing DocReplace/DocRedact use by default."""
    hits = []
    flags = re.IGNORECASE if ignore_case else 0
    try:
        pat = re.compile(query if regex else re.escape(query), flags)
    except re.error as e:
        return [{"error": f"bad regex: {e}"}]
    for b in doc.blocks:
        m = pat.search(b.text)
        if m:
            s = max(0, m.start() - 40)
            snippet = b.text[s:m.end() + 40].replace("\n", " ")
            hits.append({"id": b.id, "type": b.type, "snippet": snippet})
            if len(hits) >= max_hits:
                break
    return hits


def read(doc: Document, block_id: str, before: int = 0, after: int = 0) -> dict:
    """Return a block plus N neighbours on each side (window into the doc)."""
    i = doc.index_of(block_id)
    if i < 0:
        return {"error": f"no such block: {block_id}"}
    lo, hi = max(0, i - before), min(len(doc.blocks), i + after + 1)
    window = [{"id": b.id, "type": b.type, "hash": b.content_hash,
               "text": b.text, "target": b.id == block_id}
              for b in doc.blocks[lo:hi]]
    return {"target": block_id, "window": window}


# ---------------------------------------------------------------------------
# Transactional patching
# ---------------------------------------------------------------------------
class PatchError(Exception):
    pass


_OPS = {"replace", "insert_before", "insert_after", "delete"}


def apply_patches(doc: Document, patches: list) -> list:
    """Validate + apply a list of patches to `doc` in order, atomically: if ANY
    patch fails validation the document is left untouched and PatchError is
    raised. Each patch: {op, target_id, expected_hash?, new_text?, reason?}.

    Returns a per-patch changelog (for the audit trail + diff)."""
    # 1) validate everything first (atomic)
    for p in patches:
        op = p.get("op")
        if op not in _OPS:
            raise PatchError(f"unknown op: {op!r} (use {sorted(_OPS)})")
        tid = p.get("target_id")
        idx = doc.index_of(tid) if tid else -1
        if idx < 0:
            raise PatchError(f"target_id not found: {tid!r}")
        exp = p.get("expected_hash")
        if exp and doc.blocks[idx].content_hash != exp:
            raise PatchError(
                f"stale content for {tid}: expected_hash {exp} but current is "
                f"{doc.blocks[idx].content_hash}. Re-read the block before patching.")
        if op in ("replace", "insert_before", "insert_after") and not p.get("new_text", "").strip():
            raise PatchError(f"{op} on {tid} needs non-empty new_text")

    # 2) apply (target ids are resolved fresh each step; inserts shift indices)
    changelog = []
    for p in patches:
        op, tid = p["op"], p["target_id"]
        idx = doc.index_of(tid)
        if op == "replace":
            old = doc.blocks[idx].text
            doc.blocks[idx].text = p["new_text"]
            doc.blocks[idx].rehash()
            changelog.append({"op": op, "id": tid, "old": old, "new": p["new_text"],
                              "reason": p.get("reason", "")})
        elif op == "delete":
            old = doc.blocks.pop(idx)
            changelog.append({"op": op, "id": tid, "old": old.text, "new": "",
                              "reason": p.get("reason", "")})
        else:  # insert_before / insert_after
            btype = p.get("block_type", "paragraph")
            nb = Block(id=doc._new_id(btype), type=btype, text=p["new_text"],
                       level=int(p.get("level", 0)))
            at = idx if op == "insert_before" else idx + 1
            doc.blocks.insert(at, nb)
            changelog.append({"op": op, "id": nb.id, "anchor": tid, "old": "",
                              "new": nb.text, "reason": p.get("reason", "")})
    return changelog


def diff_text(changelog: list) -> str:
    """Human-readable unified-ish diff of a changelog."""
    import difflib
    out = []
    for c in changelog:
        head = f"@@ {c['op']} {c.get('id','')}"
        if c.get("anchor"):
            head += f" (anchor {c['anchor']})"
        if c.get("reason"):
            head += f"  — {c['reason']}"
        out.append(head)
        old = (c.get("old") or "").splitlines()
        new = (c.get("new") or "").splitlines()
        for line in difflib.unified_diff(old, new, lineterm="", n=1):
            if line.startswith(("---", "+++", "@@")):
                continue
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Bulk search-and-update (one O(n) pass — the scalable primitive for global
# changes on a large document, instead of one patch call per occurrence)
# ---------------------------------------------------------------------------
def replace_all(doc: Document, query: str, replacement: str,
                regex: bool = False, ignore_case: bool = False) -> dict:
    """Replace EVERY occurrence of `query` across all blocks in a single pass.
    Case-SENSITIVE by default (precise; preserves other casings); pass
    ignore_case=True to replace all case variants. Returns a count + touched ids."""
    flags = re.IGNORECASE if ignore_case else 0
    try:
        pat = re.compile(query if regex else re.escape(query), flags)
    except re.error as e:
        raise PatchError(f"bad regex: {e}")
    count = 0
    touched = []
    for b in doc.blocks:
        new, n = pat.subn(replacement, b.text)
        if n:
            b.text = new
            b.rehash()
            count += n
            touched.append(b.id)
    return {"replacements": count, "blocks_touched": touched}


# ---------------------------------------------------------------------------
# Redaction ("red boxing") — PERMANENTLY remove text, then VERIFY it cannot be
# recovered from the document. Verification is BLOCKING (unlike the advisory
# validators): a failed check aborts and the store is left unchanged.
# ---------------------------------------------------------------------------
REDACT_MARKER = "[REDACTED]"


def redact(doc: Document, query: str | None = None, block_id: str | None = None,
           regex: bool = False, marker: str = REDACT_MARKER,
           ignore_case: bool = True) -> dict:
    """Redact by whole block (block_id) or by matched text (query). The removed
    text is permanently replaced by `marker`; only a HASH of it is recorded (the
    store never keeps the sensitive text). Then verifies none of the removed
    strings survive in the rendered document — raises PatchError if any do.
    Case-INSENSITIVE by default so every casing of a secret is caught."""
    removed: list[str] = []
    if block_id:
        b = doc.get(block_id)
        if b is None:
            raise PatchError(f"no such block: {block_id}")
        removed.append(b.text)
        b.text = marker
        b.rehash()
        count = 1
    elif query:
        if not regex and query in marker:
            raise PatchError("redaction marker contains the query — pick a different marker")
        try:
            pat = re.compile(query if regex else re.escape(query),
                             re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise PatchError(f"bad regex: {e}")
        count = 0
        for b in doc.blocks:
            def _sub(m):
                nonlocal count
                removed.append(m.group(0))
                count += 1
                return marker
            b.text = pat.subn(_sub, b.text)[0]
            b.rehash()
        if count == 0:
            raise PatchError(f"nothing matched {query!r} — nothing redacted")
    else:
        raise PatchError("redact needs a block_id or a query")

    # BLOCKING verification: no removed string may still be recoverable.
    full = render(doc)
    leaked = sorted({s for s in removed if s and s != marker and s in full})
    if leaked:
        raise PatchError(
            f"redaction verification FAILED: {len(leaked)} removed string(s) still "
            "recoverable — redaction aborted, document unchanged")
    doc.redactions.append({"mode": "block" if block_id else "query",
                           "target": block_id or query, "count": count,
                           "content_hash": _hash("\x00".join(removed)), "marker": marker})
    return {"redacted": count, "verified": True, "marker": marker}


# ---------------------------------------------------------------------------
# Validation (deterministic, run before any LLM review)
# ---------------------------------------------------------------------------
def validate(doc: Document, required: list | None = None,
             prohibited: list | None = None) -> dict:
    """Structural + content checks. Advisory: returns pass/warn findings; callers
    decide whether to block (redaction is the only hard-blocking case, elsewhere)."""
    findings = []
    ids = [b.id for b in doc.blocks]
    dupes = {x for x in ids if ids.count(x) > 1}
    findings.append({"check": "unique_ids", "ok": not dupes,
                     "detail": f"duplicate ids: {sorted(dupes)}" if dupes else ""})
    bad_hash = [b.id for b in doc.blocks if b.content_hash != _hash(b.text)]
    findings.append({"check": "hashes_consistent", "ok": not bad_hash,
                     "detail": f"stale hashes: {bad_hash}" if bad_hash else ""})
    empty = [b.id for b in doc.blocks if not b.text.strip()]
    findings.append({"check": "no_empty_blocks", "ok": not empty,
                     "detail": f"empty blocks: {empty}" if empty else ""})
    full = render(doc)
    for phrase in (required or []):
        findings.append({"check": f"required:{phrase!r}", "ok": phrase in full,
                         "detail": "" if phrase in full else "missing"})
    for phrase in (prohibited or []):
        findings.append({"check": f"prohibited:{phrase!r}", "ok": phrase not in full,
                         "detail": "" if phrase not in full else "still present"})
    findings.append({"check": "heading_levels_monotonic",
                     "ok": _levels_ok(doc), "detail": _levels_detail(doc)})
    return {"ok": all(f["ok"] for f in findings), "findings": findings}


def _levels_ok(doc: Document) -> bool:
    prev = 0
    for b in doc.blocks:
        if b.type == "heading":
            if prev and b.level > prev + 1:
                return False
            prev = b.level
    return True


def _levels_detail(doc: Document) -> str:
    prev = 0
    for b in doc.blocks:
        if b.type == "heading":
            if prev and b.level > prev + 1:
                return f"{b.id} jumps from h{prev} to h{b.level}"
            prev = b.level
    return ""


# ---------------------------------------------------------------------------
# Commit (canonical model -> source file, original preserved)
# ---------------------------------------------------------------------------
def commit(doc: Document) -> dict:
    """Render the canonical model back to the source file. On the first commit,
    the untouched original is preserved as ``<source>.orig`` (immutable artifact)."""
    src = Path(doc.source_path)
    if not doc.committed:
        orig = src.with_suffix(src.suffix + ".orig")
        if src.exists() and not orig.exists():
            orig.write_text(src.read_text(encoding="utf-8", errors="replace"),
                            encoding="utf-8")
        doc.committed = True
    text = render(doc)
    src.write_text(text, encoding="utf-8")
    doc.source_hash = _hash(text)
    save(doc)
    return {"source": str(src), "blocks": len(doc.blocks),
            "original": str(src.with_suffix(src.suffix + ".orig"))}
