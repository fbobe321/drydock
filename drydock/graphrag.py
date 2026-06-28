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
from collections import Counter, defaultdict

from drydock import extract
from pathlib import Path

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
    """Project-local index (travels with the project, easy to .gitignore)."""
    return Path(cwd) / ".drydock" / "graphrag.json"


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


def _iter_text_files(paths: list[str]):
    for p in paths:
        path = Path(p)
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


def _ingest_files(paths, cwd, chunks, entity_chunks, edges, skip_sources):
    """Chunk + extract + graph every text file under paths into the (mutable)
    accumulators, skipping any source already present. Returns files added."""
    added = 0
    for fp in _iter_text_files([str(Path(cwd) / p) if not os.path.isabs(p) else p
                                for p in paths]):
        rel = os.path.relpath(str(fp), cwd)
        if rel in skip_sources:
            continue
        if fp.suffix.lower() in extract.EXTRACTABLE_EXT:
            text = extract.extract_document(fp)  # PDF/Word → text (or None)
            if not text:
                continue  # unreadable / no PDF backend — skip cleanly
        else:
            try:
                text = fp.read_text("utf-8", "ignore")
            except OSError:
                continue
        if not text.strip():
            continue
        added += 1
        skip_sources.add(rel)  # don't double-ingest the same file in one call
        for body in _chunk_text(text):
            cid = len(chunks)
            ents = extract_entities(body)
            chunks.append({"id": cid, "source": rel, "text": body, "entities": ents})
            for e in ents:
                entity_chunks[e].add(cid)
            for i, a in enumerate(ents):  # co-occurrence edges within the chunk
                for b in ents[i + 1:]:
                    edges[a][b] += 1
                    edges[b][a] += 1
    return added


def _save(store_path, chunks, entity_chunks, edges, files_added):
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


def build_index(paths: list[str], store_path: str | Path, *, cwd: str = ".") -> dict:
    """Build (or REBUILD from scratch) the knowledge graph from paths; persist to
    store_path. Returns {files, chunks, entities, edges}."""
    chunks: list[dict] = []
    entity_chunks: dict[str, set[int]] = defaultdict(set)
    edges: dict[str, Counter] = defaultdict(Counter)
    added = _ingest_files(paths, cwd, chunks, entity_chunks, edges, set())
    return _save(store_path, chunks, entity_chunks, edges, added)


def add_to_index(paths: list[str], store_path: str | Path, *, cwd: str = ".") -> dict:
    """Incrementally ADD documents to an existing index (build it if none yet).
    Files already indexed (by relative path) are skipped — clear+build to refresh
    changed files. Returns {files (added), chunks, entities, edges} totals."""
    existing = load_index(store_path)
    if existing is None:
        return build_index(paths, store_path, cwd=cwd)
    chunks: list[dict] = list(existing.get("chunks", []))
    entity_chunks: dict[str, set[int]] = defaultdict(set)
    for e, cids in existing.get("entities", {}).items():
        entity_chunks[e] = set(cids)
    edges: dict[str, Counter] = defaultdict(Counter)
    for a, nbrs in existing.get("edges", {}).items():
        edges[a] = Counter(nbrs)
    skip = {c["source"] for c in chunks}
    added = _ingest_files(paths, cwd, chunks, entity_chunks, edges, skip)
    return _save(store_path, chunks, entity_chunks, edges, added)


def sources(index: dict) -> list[str]:
    """The distinct source files in an index, sorted."""
    return sorted({c["source"] for c in index.get("chunks", [])})


def load_index(store_path: str | Path) -> dict | None:
    sp = Path(store_path)
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def query_index(index: dict, query: str, *, k: int = 5, hops: bool = True) -> dict:
    """Retrieve the top-k chunks for a query, expanded one hop over the graph.

    Returns {chunks: [{source, text, score}], related: [entities]}.
    """
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
