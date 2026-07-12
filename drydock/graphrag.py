"""GraphRAG — a local, dependency-free knowledge graph for retrieval.

The user builds an index from their own docs/code; the agent then queries it
through the `Knowledge` tool while answering or coding, so it can ground its
work in project-specific information the model was never trained on.

Design (clean-room, stdlib only — no embeddings, no third-party deps, works
against any server):
  • Ingest text files → split into chunks.
  • Extract ENTITIES from each chunk (proper nouns, `code` terms, CamelCase /
    snake_case identifiers, ACRONYMS) → graph NODES.
  • Entities co-occurring in a chunk get an EDGE (weighted by co-occurrence) →
    the graph. Each entity also points at the chunks it appears in.
  • Query: pull the query's entities + keywords, score chunks by entity and
    keyword overlap, then EXPAND one hop along the entity graph so strongly
    related context comes along — the "graph" in GraphRAG. Return the top
    chunks plus the related entities, both fed back to the model.

The index is a single JSON file so it's inspectable, portable, and gitignorable.

All logic original to Drydock.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from drydock import extract

# Files we ingest as text. Everything else (binaries, images) is skipped.
_TEXT_EXT = {
    ".md", ".txt", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go",
    ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".toml",
    ".yaml", ".yml", ".json", ".cfg", ".ini", ".sql", ".html", ".css", ".org",
    ".tex", ".csv", ".log", ".",
}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
              "build", ".mypy_cache", ".pytest_cache", ".drydock"}

_STOPWORDS = frozenset("""
the a an and or but if then else for while of to in on at by with from into as
is are was were be been being do does did this that these those it its their
your you we they he she his her our not no yes can will would should could may
""".split())

# Entity patterns (kept simple + fast; tuned to surface useful nodes, not noise)
_RE_BACKTICK = re.compile(r"`([^`\n]{2,60})`")
_RE_IDENT = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*(?:[_./][a-zA-Z0-9_]+)+)\b")
_RE_CAMEL = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b")
_RE_PROPER = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:[ \t]+[A-Z][a-zA-Z0-9]+){0,3})\b")
_RE_ACRONYM = re.compile(r"\b([A-Z]{2,6})\b")
# Dotted/hyphenated codes like control IDs (AC-2, AC-2.1, SI-4), CVEs, STIG IDs —
# these tokenize into too-short pieces otherwise, so a lookup by the code misses.
_RE_CODE = re.compile(r"\b([A-Za-z]{1,6}-\d+(?:\.\d+)*)\b")
_RE_WORD = re.compile(r"[a-zA-Z0-9_]+")


def default_store_path(cwd: str) -> Path:
    """Project-local index. SQLite so queries touch only matching rows (fast even
    at multi-GB scale) instead of parsing a giant JSON on every query."""
    return Path(cwd) / ".drydock" / "graphrag.db"


def _resolve_store(store_path) -> Path:
    """Given a requested store path, return the one that actually exists — a
    ``.db`` (SQLite) if present, else a legacy ``.json`` sibling, else the path
    as given (a fresh build will create it)."""
    p = Path(store_path)
    if p.exists():
        return p
    db = p.with_suffix(".db")
    if db.exists():
        return db
    js = p.with_suffix(".json")
    if js.exists():
        return js
    return p


# ── SQLite + FTS5 backend (the scalable store) ────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, source TEXT, text TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TABLE IF NOT EXISTS entities(entity TEXT, chunk_id INTEGER);
CREATE INDEX IF NOT EXISTS idx_entity ON entities(entity);
CREATE TABLE IF NOT EXISTS edges(a TEXT, b TEXT, weight INTEGER);
CREATE INDEX IF NOT EXISTS idx_edge_a ON edges(a);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

_FTS_STRIP = re.compile(r'[^A-Za-z0-9_ ]+')


def _is_sqlite(path) -> bool:
    return str(path).endswith(".db")


def _write_sqlite(store_path, chunks, entity_chunks, edges) -> None:
    """(Re)build the SQLite store from the accumulated chunks/entities/edges."""
    sp = Path(store_path)
    sp.parent.mkdir(parents=True, exist_ok=True)
    if sp.exists():
        sp.unlink()
    con = sqlite3.connect(str(sp))
    try:
        con.executescript(_SCHEMA)
        con.executemany("INSERT INTO chunks(id,source,text) VALUES(?,?,?)",
                        ((c["id"], c["source"], c["text"]) for c in chunks))
        con.executemany("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)",
                        ((c["id"], c["text"]) for c in chunks))
        con.executemany("INSERT INTO entities(entity,chunk_id) VALUES(?,?)",
                        ((e, cid) for e, cids in entity_chunks.items() for cid in cids))
        con.executemany("INSERT INTO edges(a,b,weight) VALUES(?,?,?)",
                        ((a, b, w) for a, nbrs in edges.items() for b, w in nbrs.items()))
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('version','2')")
        con.commit()
    finally:
        con.close()


def _fts_query(words) -> str:
    """Build a safe FTS5 MATCH expression (OR of quoted terms)."""
    terms = []
    for w in words:
        w = _FTS_STRIP.sub(" ", w).strip()
        if w:
            terms.append('"' + w + '"')
    return " OR ".join(terms)


def _query_sqlite(store_path, query: str, k: int, hops: bool) -> dict:
    """Fast query: FTS5 full-text over chunk text + indexed entity/graph lookups.
    Loads only the matching rows — no full-file parse."""
    con = sqlite3.connect(str(store_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        q_entities = set(extract_entities(query))
        q_words = {w for w in (w.lower() for w in _RE_WORD.findall(query))
                   if len(w) > 2 and w not in _STOPWORDS}
        scores: dict[int, float] = defaultdict(float)

        # 1) FTS5 keyword retrieval (indexed, bm25-ranked) — the main recall path.
        expr = _fts_query(q_words)
        if expr:
            try:
                rows = cur.execute(
                    "SELECT rowid, rank FROM chunks_fts WHERE chunks_fts MATCH ? "
                    "ORDER BY rank LIMIT 400", (expr,)).fetchall()
                for r in rows:
                    scores[r["rowid"]] += 0.5 + min(2.0, -float(r["rank"]) / 4.0)
            except sqlite3.OperationalError:
                pass

        # 2) Exact entity hits (indexed) weigh most; collect matched entities.
        matched: set[str] = set()
        cand = {e.lower() for e in q_entities} | q_words
        for qe in cand:
            for r in cur.execute("SELECT chunk_id, entity FROM entities WHERE entity=?", (qe,)):
                scores[r["chunk_id"]] += 3.0
                matched.add(r["entity"])

        # 3) 1-hop graph expansion over the strongest neighbors of matched entities.
        related: list[str] = []
        if hops and matched:
            for e in list(matched):
                nbrs = cur.execute("SELECT b, weight FROM edges WHERE a=? ORDER BY weight DESC LIMIT 5",
                                   (e,)).fetchall()
                for nb in nbrs:
                    if nb["b"] not in matched:
                        related.append(nb["b"])
                        for r in cur.execute("SELECT chunk_id FROM entities WHERE entity=?", (nb["b"],)):
                            scores[r["chunk_id"]] += 1.0

        if not scores:
            return {"chunks": [], "related": []}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        out = []
        for cid, sc in ranked:
            if sc <= 0:
                continue
            row = cur.execute("SELECT source, text FROM chunks WHERE id=?", (cid,)).fetchone()
            if row:
                out.append({"source": row["source"], "text": row["text"], "score": round(sc, 1)})
        seen: set[str] = set()
        rel_unique = [r for r in related if not (r in seen or seen.add(r))][:12]
        return {"chunks": out, "related": rel_unique}
    finally:
        con.close()


def migrate_json_to_sqlite(json_path, db_path) -> dict:
    """One-time conversion of a legacy JSON index to the SQLite store. Loads the
    JSON once (slow for a huge file, but one-time), then queries are fast."""
    data = json.loads(Path(json_path).read_text("utf-8"))
    chunks = data.get("chunks", [])
    entity_chunks = {e: list(cids) for e, cids in data.get("entities", {}).items()}
    edges = {a: dict(nbrs) for a, nbrs in data.get("edges", {}).items()}
    _write_sqlite(db_path, chunks, entity_chunks, edges)
    return {"chunks": len(chunks), "entities": len(entity_chunks),
            "edges": sum(len(n) for n in edges.values())}


def extract_entities(text: str) -> list[str]:
    """Heuristic entity extraction → normalized (lowercased) entity keys."""
    found: set[str] = set()
    for rx in (_RE_BACKTICK, _RE_IDENT, _RE_CAMEL, _RE_PROPER, _RE_ACRONYM, _RE_CODE):
        for m in rx.findall(text):
            e = m.strip().lower()
            # Drop trivial / stopword-only entities and overlong noise.
            if len(e) < 3 or len(e) > 60:
                continue
            if all(w in _STOPWORDS for w in _RE_WORD.findall(e)):
                continue
            found.add(e)
    return sorted(found)


def _chunk_text(text: str, max_chars: int = 1200) -> list[str]:
    """Split into paragraph-ish chunks, capping size so a chunk stays focused."""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if size + len(para) > max_chars and buf:
            chunks.append("\n\n".join(buf))
            buf, size = [], 0
        if len(para) > max_chars:  # a single huge paragraph → hard-split
            for i in range(0, len(para), max_chars):
                chunks.append(para[i:i + max_chars])
            continue
        buf.append(para)
        size += len(para)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _unquote(p: str) -> str:
    """Strip surrounding quotes a user wrapped a path in — common on Windows:
    `/graphrag build "C:\\Users\\me\\Documents"`. Without this the quote chars
    become part of the path, isabs() is False, it's joined under cwd, and nothing
    is found ("No text found. Nothing was indexed.")."""
    p = p.strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
        p = p[1:-1]
    return p


def _iter_text_files(paths: list[str]):
    for p in paths:
        path = Path(_unquote(p))
        if path.is_file():
            yield path
        elif path.is_dir():
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for f in files:
                    fp = Path(root) / f
                    ext = fp.suffix.lower()
                    if ext in _TEXT_EXT or ext in extract.EXTRACTABLE_EXT or fp.suffix == "":
                        yield fp


def _iter_file_chunks(paths, cwd, skip_sources, progress=None):
    """Yield (rel_source, [(body, entities), …]) for each ingestible file, one file
    at a time (so callers never hold every file's text at once). Per-file isolation:
    one unreadable/odd file (a locked Word doc, weird encoding, PDF quirk) is skipped,
    not fatal. Calls progress(files_done, rel) after each file so a long build can
    report instead of appearing frozen."""
    cleaned = [_unquote(p) for p in paths]
    files_done = 0
    for fp in _iter_text_files([str(Path(cwd) / p) if not os.path.isabs(p) else p
                                for p in cleaned]):
        try:
            rel = os.path.relpath(str(fp), cwd)
            if rel in skip_sources:
                continue
            if fp.suffix.lower() in extract.EXTRACTABLE_EXT:
                text = extract.extract_document(fp)  # PDF/Word → text (or None)
            else:
                text = fp.read_text("utf-8", "ignore")
            if not text or not text.strip():
                continue
            local = [(body, extract_entities(body)) for body in _chunk_text(text)]
        except Exception:  # noqa: BLE001 — isolate a bad file, never abort the build
            continue
        skip_sources.add(rel)
        files_done += 1
        if progress:
            try:
                progress(files_done, rel)
            except Exception:  # noqa: BLE001 — a progress callback must never break a build
                pass
        yield rel, local


def _ingest_files(paths, cwd, chunks, entity_chunks, edges, skip_sources, progress=None):
    """In-memory ingest into the accumulators (legacy JSON path — small indexes)."""
    added = 0
    for rel, local in _iter_file_chunks(paths, cwd, skip_sources, progress):
        added += 1
        for body, ents in local:
            cid = len(chunks)
            chunks.append({"id": cid, "source": rel, "text": body, "entities": ents})
            for e in ents:
                entity_chunks[e].add(cid)
            for i, a in enumerate(ents):  # co-occurrence edges within the chunk
                for b in ents[i + 1:]:
                    edges[a][b] += 1
                    edges[b][a] += 1
    return added


def _build_sqlite_stream(paths, store_path, cwd, skip_sources, progress=None, append=False):
    """Streaming SQLite build: chunk text is inserted per-file (never held for the
    whole corpus, so memory stays bounded even for a huge folder). Entities/edges
    aggregate in memory (far smaller than the text) and are written at the end."""
    sp = Path(store_path)
    sp.parent.mkdir(parents=True, exist_ok=True)
    if not append and sp.exists():
        sp.unlink()
    con = sqlite3.connect(str(sp))
    entity_chunks: dict[str, set[int]] = defaultdict(set)
    edges: dict[str, Counter] = defaultdict(Counter)
    added = 0
    try:
        con.executescript(_SCHEMA)
        # continue chunk ids after any existing rows (append mode)
        row = con.execute("SELECT COALESCE(MAX(id), -1) FROM chunks").fetchone()
        next_id = (row[0] if row else -1) + 1
        if append:  # seed the in-memory graph with what's already stored
            for r in con.execute("SELECT entity, chunk_id FROM entities"):
                entity_chunks[r[0]].add(r[1])
            for r in con.execute("SELECT a, b, weight FROM edges"):
                edges[r[0]][r[1]] = r[2]
            con.execute("DELETE FROM entities"); con.execute("DELETE FROM edges")
        for rel, local in _iter_file_chunks(paths, cwd, skip_sources, progress):
            added += 1
            rows = []
            for body, ents in local:
                cid = next_id; next_id += 1
                con.execute("INSERT INTO chunks(id,source,text) VALUES(?,?,?)", (cid, rel, body))
                con.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cid, body))
                for e in ents:
                    entity_chunks[e].add(cid)
                for i, a in enumerate(ents):
                    for b in ents[i + 1:]:
                        edges[a][b] += 1; edges[b][a] += 1
                rows.append(cid)
            if added % 200 == 0:
                con.commit()
        con.executemany("INSERT INTO entities(entity,chunk_id) VALUES(?,?)",
                        ((e, cid) for e, cids in entity_chunks.items() for cid in cids))
        con.executemany("INSERT INTO edges(a,b,weight) VALUES(?,?,?)",
                        ((a, b, w) for a, nbrs in edges.items() for b, w in nbrs.items()))
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('version','2')")
        con.commit()
        return {"files": added, "chunks": next_id, "entities": len(entity_chunks),
                "edges": sum(len(n) for n in edges.values()) // 2}
    finally:
        con.close()


def _save(store_path, chunks, entity_chunks, edges, files_added):
    # SQLite by default (scalable). A caller can still target a .json path
    # explicitly (small/portable indexes, tests) and get the legacy format.
    if _is_sqlite(store_path):
        _write_sqlite(store_path, chunks,
                      {e: sorted(cids) for e, cids in entity_chunks.items()},
                      {a: dict(nbrs) for a, nbrs in edges.items()})
    else:
        index = {
            "version": 1,
            "chunks": chunks,
            "entities": {e: sorted(cids) for e, cids in entity_chunks.items()},
            "edges": {a: dict(nbrs) for a, nbrs in edges.items()},
        }
        sp = Path(store_path)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(index), encoding="utf-8")
    return {
        "files": files_added,
        "chunks": len(chunks),
        "entities": len(entity_chunks),
        "edges": sum(len(v) for v in edges.values()) // 2,
    }


def build_index(paths: list[str], store_path: str | Path, *, cwd: str = ".",
                progress=None) -> dict:
    """Build (or REBUILD from scratch) the knowledge graph from paths; persist to
    store_path. Returns {files, chunks, entities, edges}. `progress(files_done, src)`
    is called per file (for a UI). SQLite stores stream to disk (bounded memory)."""
    if _is_sqlite(store_path):
        return _build_sqlite_stream(paths, store_path, cwd, set(), progress, append=False)
    chunks: list[dict] = []
    entity_chunks: dict[str, set[int]] = defaultdict(set)
    edges: dict[str, Counter] = defaultdict(Counter)
    added = _ingest_files(paths, cwd, chunks, entity_chunks, edges, set(), progress)
    return _save(store_path, chunks, entity_chunks, edges, added)


def _read_all(store_path) -> dict:
    """Read a whole index into a dict (chunks/entities/edges) — used by add_to_index
    to append. Handles both the SQLite store and a legacy JSON file."""
    sp = _resolve_store(store_path)
    if _is_sqlite(sp):
        con = sqlite3.connect(str(sp))
        con.row_factory = sqlite3.Row
        try:
            chunks = [{"id": r["id"], "source": r["source"], "text": r["text"]}
                      for r in con.execute("SELECT id,source,text FROM chunks")]
            entities: dict[str, list[int]] = defaultdict(list)
            for r in con.execute("SELECT entity,chunk_id FROM entities"):
                entities[r["entity"]].append(r["chunk_id"])
            edges: dict[str, dict] = defaultdict(dict)
            for r in con.execute("SELECT a,b,weight FROM edges"):
                edges[r["a"]][r["b"]] = r["weight"]
            return {"chunks": chunks, "entities": entities, "edges": edges}
        finally:
            con.close()
    return json.loads(Path(sp).read_text("utf-8"))


def add_to_index(paths: list[str], store_path: str | Path, *, cwd: str = ".",
                 progress=None) -> dict:
    """Incrementally ADD documents to an existing index (build it if none yet).
    Files already indexed (by relative path) are skipped — clear+build to refresh
    changed files. Returns {files (added), chunks, entities, edges} totals."""
    sp = _resolve_store(store_path)
    if not sp.exists():
        return build_index(paths, store_path, cwd=cwd, progress=progress)
    if _is_sqlite(sp):
        skip = {r[0] for r in sqlite3.connect(str(sp)).execute("SELECT DISTINCT source FROM chunks")}
        return _build_sqlite_stream(paths, sp, cwd, skip, progress, append=True)
    existing = _read_all(sp)
    chunks: list[dict] = list(existing.get("chunks", []))
    entity_chunks: dict[str, set[int]] = defaultdict(set)
    for e, cids in existing.get("entities", {}).items():
        entity_chunks[e] = set(cids)
    edges: dict[str, Counter] = defaultdict(Counter)
    for a, nbrs in existing.get("edges", {}).items():
        edges[a] = Counter(nbrs)
    skip = {c["source"] for c in chunks}
    added = _ingest_files(paths, cwd, chunks, entity_chunks, edges, skip, progress)
    return _save(sp, chunks, entity_chunks, edges, added)


def sources(index: dict) -> list[str]:
    """The distinct source files in an index, sorted. Accepts a loaded JSON dict
    OR a SQLite handle ({'_db': path})."""
    db = index.get("_db")
    if db:
        con = sqlite3.connect(str(db))
        try:
            return sorted(r[0] for r in con.execute("SELECT DISTINCT source FROM chunks"))
        finally:
            con.close()
    return sorted({c["source"] for c in index.get("chunks", [])})


def index_stats(index: dict) -> dict:
    """{chunks, entities} counts for a loaded dict OR a SQLite handle — without
    reading a large index into memory."""
    db = index.get("_db")
    if db:
        con = sqlite3.connect(str(db))
        try:
            c = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            e = con.execute("SELECT COUNT(DISTINCT entity) FROM entities").fetchone()[0]
            return {"chunks": c, "entities": e}
        finally:
            con.close()
    return {"chunks": len(index.get("chunks", [])), "entities": len(index.get("entities", {}))}


def load_index(store_path: str | Path):
    """Return a query handle for the index at store_path, or None if none exists.

    For the SQLite store this is a tiny handle ({'_db': path}) — it does NOT read
    the data (queries hit the DB directly, so a multi-GB index stays instant). For
    a legacy JSON file it loads the dict (small/portable indexes)."""
    sp = _resolve_store(store_path)
    if not sp.exists():
        return None
    if _is_sqlite(sp):
        return {"_db": str(sp)}
    try:
        return json.loads(Path(sp).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def query_index(index: dict, query: str, *, k: int = 5, hops: bool = True) -> dict:
    """Retrieve the top-k chunks for a query, expanded one hop over the graph.

    Returns {chunks: [{source, text, score}], related: [entities]}.
    """
    # SQLite handle → the fast, indexed path (no full-file load).
    db = index.get("_db")
    if db:
        return _query_sqlite(db, query, k, hops)

    chunks = index.get("chunks", [])
    entities = index.get("entities", {})
    edges = index.get("edges", {})
    if not chunks:
        return {"chunks": [], "related": []}

    q_entities = set(extract_entities(query))
    q_words = {w for w in (w.lower() for w in _RE_WORD.findall(query))
               if len(w) > 2 and w not in _STOPWORDS}

    # Match query entities to graph entities (exact + substring both ways).
    matched: set[str] = set()
    for qe in q_entities | q_words:
        for ge in entities:
            if qe == ge or qe in ge or ge in qe:
                matched.add(ge)

    # 1-hop graph expansion: pull the strongest neighbors of matched entities.
    related: list[str] = []
    expanded: set[str] = set(matched)
    if hops:
        for e in list(matched):
            nbrs = sorted(edges.get(e, {}).items(), key=lambda kv: -kv[1])[:5]
            for nbr, _w in nbrs:
                expanded.add(nbr)
                if nbr not in matched:
                    related.append(nbr)

    # Score chunks: direct entity hits weigh most, then graph-neighbor hits,
    # then raw keyword overlap (so retrieval still works if entities miss).
    scores: dict[int, float] = defaultdict(float)
    for e in matched:
        for cid in entities.get(e, []):
            scores[cid] += 3.0
    for e in expanded - matched:
        for cid in entities.get(e, []):
            scores[cid] += 1.0
    for c in chunks:
        if q_words:
            overlap = len(q_words & {w.lower() for w in _RE_WORD.findall(c["text"])})
            if overlap:
                scores[c["id"]] += 0.5 * overlap

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
    out_chunks = [
        {"source": chunks[cid]["source"], "text": chunks[cid]["text"], "score": round(sc, 1)}
        for cid, sc in ranked if sc > 0
    ]
    # de-dup related, keep order, cap
    seen: set[str] = set()
    rel_unique = [r for r in related if not (r in seen or seen.add(r))][:12]
    return {"chunks": out_chunks, "related": rel_unique}


def format_results(result: dict, query: str) -> str:
    """Render query results as the tool's string output for the model."""
    chunks = result.get("chunks", [])
    if not chunks:
        return (
            f"No knowledge-base matches for: {query!r}. The index may not cover "
            "this topic — answer from your own knowledge, or ask the user to add "
            "the relevant docs with /graphrag build <path>."
        )
    parts = [f"Knowledge base — {len(chunks)} relevant passage(s) for {query!r}:"]
    for i, c in enumerate(chunks, 1):
        parts.append(f"\n[{i}] (source: {c['source']}, score {c['score']})\n{c['text']}")
    if result.get("related"):
        parts.append("\nRelated entities in the graph: " + ", ".join(result["related"]))
    return "\n".join(parts)
