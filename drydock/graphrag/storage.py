"""SQLite storage + pure-Python TF-IDF retrieval for GraphRAG.

`Index` is the v0 backend behind the `Retriever` protocol. It owns a
single SQLite file and exposes:

    idx = Index(db_path)
    idx.ingest_path("/path/to/repo")            # idempotent
    result = idx.retrieve("how does the cache invalidate?")
    hits = idx.find_symbol("Request")
    chain = idx.inheritance_chain("flask.wrappers.Request")

TF-IDF math is intentionally simple — no numpy/sklearn dependency, all
in stdlib. Good enough to validate the interface and the failure-mode
mitigations from TRIAGE_v1.md. A v1 swap to embeddings goes through the
same `Retriever` protocol; the harness doesn't care which backend ran.

Schema (kept minimal — additive migrations are a Phase-3 concern):

    symbols(id, name, qualname, kind, file, line, end_line,
            parents_csv, docstring)
    text_chunks(id, file, start_line, end_line, content)
    text_terms(chunk_id, term, tf)             -- chunk-local term freq
    text_df(term, df)                          -- corpus document freq
    meta(key, value)                            -- {N: total chunks}
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import json
from datetime import datetime, timezone

from drydock.graphrag.code_indexer import SymbolRecord, index_path as index_code
from drydock.graphrag.retriever import (
    RetrievalResult,
    SymbolHit,
    TextHit,
    WorkedExampleHit,
)
from drydock.graphrag.text_indexer import TextChunk, index_path as index_text


_SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    qualname TEXT NOT NULL,
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    parents_csv TEXT NOT NULL DEFAULT '',
    docstring TEXT NOT NULL DEFAULT '',
    UNIQUE(file, line, qualname)
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualname ON symbols(qualname);

CREATE TABLE IF NOT EXISTS text_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(file, start_line, end_line)
);

CREATE TABLE IF NOT EXISTS text_terms (
    chunk_id INTEGER NOT NULL,
    term TEXT NOT NULL,
    tf INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, term),
    FOREIGN KEY (chunk_id) REFERENCES text_chunks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_text_terms_term ON text_terms(term);

CREATE TABLE IF NOT EXISTS text_df (
    term TEXT PRIMARY KEY,
    df INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- "Second brain" payload: previously-solved problems with full reasoning
-- chains. The model retrieves these alongside flat-chunk text so it sees
-- not just facts but how analogous problems were worked through.
CREATE TABLE IF NOT EXISTS worked_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    -- JSON-encoded list[str]; ordered first→last reasoning step
    reasoning_json TEXT NOT NULL DEFAULT '[]',
    final_answer TEXT NOT NULL DEFAULT '',
    -- Provenance: "manual", "hle:<qid>", "session:<sid>", etc.
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    -- Dedup key: same problem text + same source = single canonical row.
    UNIQUE(problem_text, source)
);
CREATE INDEX IF NOT EXISTS idx_worked_examples_category
    ON worked_examples(category);
CREATE INDEX IF NOT EXISTS idx_worked_examples_source
    ON worked_examples(source);

-- TF-IDF on the *problem statement only* (not the reasoning chain). We
-- want a query like "Gauss-Bonnet coupling holographic D3/D7" to match
-- problems that share that physical setup, not problems whose answer
-- chains happen to mention "Gauss" downstream.
CREATE TABLE IF NOT EXISTS worked_example_terms (
    example_id INTEGER NOT NULL,
    term TEXT NOT NULL,
    tf INTEGER NOT NULL,
    PRIMARY KEY (example_id, term),
    FOREIGN KEY (example_id) REFERENCES worked_examples(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_worked_example_terms_term
    ON worked_example_terms(term);

CREATE TABLE IF NOT EXISTS worked_example_df (
    term TEXT PRIMARY KEY,
    df INTEGER NOT NULL
);
"""

# Token regex: word chars + apostrophe. Lowercased, length-filtered.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_']*")
_MIN_TOKEN_LEN = 2

# English stopwords. Filtered at query time only — the index keeps them
# (DF math benefits from knowing they exist) but query scoring ignores
# them so a short narrow-trivia question like "In the 1997 movie X..."
# doesn't get scored on "in", "the", "movie" which match almost every
# chunk in any corpus and drown out the actual signal token (X).
# Diagnosed by HLE Phase 0 ablation: Q5 (Ovosodo→Boston) and Q12
# (Nunavut) failed because BM25 surfaced unrelated chunks as top-1.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "can", "could", "did", "do", "does", "don't", "for", "from", "had",
    "has", "have", "he", "her", "here", "hers", "him", "his", "how",
    "i", "if", "in", "into", "is", "it", "its", "just", "may", "me",
    "might", "my", "no", "nor", "not", "now", "of", "on", "or", "our",
    "out", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "us", "was", "we", "were",
    "what", "when", "where", "which", "while", "who", "whom", "why",
    "will", "with", "would", "you", "your", "yours",
    # HLE-prompt-template wrappers (always present, never useful)
    "answer", "answers", "question", "choice", "choices", "give",
    "following", "options", "option",
})


def _tokenize(text: str) -> list[str]:
    return [
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if len(t) >= _MIN_TOKEN_LEN
    ]


def _tokenize_query(text: str) -> list[str]:
    """Query-side tokenizer: drops English stopwords + HLE-prompt-template
    boilerplate that match every chunk uselessly. Index-side tokenizer
    (`_tokenize`) keeps stopwords because DF accounting still needs them
    when computing IDF for chunk content."""
    return [t for t in _tokenize(text) if t not in _STOPWORDS]


class Index:
    """SQLite-backed v0 retriever. Implements the `Retriever` protocol."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as cx:
            cx.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        cx = sqlite3.connect(self.db_path)
        cx.execute("PRAGMA foreign_keys = ON")
        try:
            yield cx
            cx.commit()
        finally:
            cx.close()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_path(self, root: str | Path) -> dict[str, int]:
        """Ingest all .py + .md/.txt files under `root`. Idempotent: a
        re-ingest of the same root replaces records for files that
        re-appear (so editing a file then re-ingesting refreshes
        symbols / chunks).

        Returns a count summary."""
        root = Path(root).resolve()
        symbol_count = 0
        chunk_count = 0
        files_seen: set[str] = set()

        # Collect first so we can wipe per-file before inserting.
        symbols = list(index_code(root))
        chunks = list(index_text(root))

        for s in symbols:
            files_seen.add(s.file)
        for c in chunks:
            files_seen.add(c.file)

        with self._connect() as cx:
            # Clear existing rows for files we're about to re-ingest.
            for f in files_seen:
                cx.execute("DELETE FROM symbols WHERE file = ?", (f,))
                cx.execute(
                    "DELETE FROM text_chunks WHERE file = ?", (f,)
                )
            # Cascading delete on text_terms via FK.

            for s in symbols:
                cx.execute(
                    """INSERT OR IGNORE INTO symbols
                       (name, qualname, kind, file, line, end_line,
                        parents_csv, docstring)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (s.name, s.qualname, s.kind, s.file, s.line,
                     s.end_line, ",".join(s.parents), s.docstring),
                )
                symbol_count += 1

            for c in chunks:
                cur = cx.execute(
                    """INSERT OR IGNORE INTO text_chunks
                       (file, start_line, end_line, content)
                       VALUES (?, ?, ?, ?)""",
                    (c.file, c.start_line, c.end_line, c.content),
                )
                if cur.rowcount == 0:
                    continue
                chunk_id = cur.lastrowid
                tokens = _tokenize(c.content)
                term_freq = Counter(tokens)
                for term, tf in term_freq.items():
                    cx.execute(
                        """INSERT INTO text_terms (chunk_id, term, tf)
                           VALUES (?, ?, ?)""",
                        (chunk_id, term, tf),
                    )
                chunk_count += 1

            self._recompute_df(cx)

        return {
            "symbols": symbol_count,
            "chunks": chunk_count,
            "files": len(files_seen),
        }

    def _recompute_df(self, cx: sqlite3.Connection) -> None:
        cx.execute("DELETE FROM text_df")
        cx.execute(
            """INSERT INTO text_df(term, df)
               SELECT term, COUNT(DISTINCT chunk_id) FROM text_terms
               GROUP BY term"""
        )
        total = cx.execute(
            "SELECT COUNT(*) FROM text_chunks"
        ).fetchone()[0]
        cx.execute("DELETE FROM meta WHERE key = 'N'")
        cx.execute(
            "INSERT INTO meta(key, value) VALUES ('N', ?)", (str(total),)
        )

    # ------------------------------------------------------------------
    # Symbol lookup
    # ------------------------------------------------------------------

    def find_symbol(self, name: str) -> list[SymbolHit]:
        """Match by exact name, by qualname suffix, or by qualname equality."""
        with self._connect() as cx:
            rows = cx.execute(
                """SELECT name, qualname, kind, file, line, parents_csv, docstring
                   FROM symbols
                   WHERE name = ?
                      OR qualname = ?
                      OR qualname LIKE ?
                   ORDER BY (qualname = ?) DESC, length(qualname) ASC
                   LIMIT 25""",
                (name, name, f"%.{name}", name),
            ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    def inheritance_chain(self, qualname: str) -> list[SymbolHit]:
        """Walk the parent chain. Best-effort: matches parents by bare
        name OR qualname. Stops on cycles or unknown ancestor."""
        chain: list[SymbolHit] = []
        seen: set[str] = set()
        current = qualname
        for _ in range(10):  # cycle guard
            if current in seen:
                break
            seen.add(current)
            hit = self._lookup_one_class(current)
            if hit is None:
                break
            chain.append(hit)
            if not hit.parents:
                break
            # Walk to the first base class only — multi-inheritance trees
            # would need a more elaborate output shape than v0 has.
            current = hit.parents[0]
        return chain

    def _lookup_one_class(self, name: str) -> SymbolHit | None:
        with self._connect() as cx:
            row = cx.execute(
                """SELECT name, qualname, kind, file, line, parents_csv, docstring
                   FROM symbols
                   WHERE kind = 'class'
                     AND (qualname = ? OR name = ?)
                   ORDER BY (qualname = ?) DESC, length(qualname) ASC
                   LIMIT 1""",
                (name, name, name),
            ).fetchone()
        return self._row_to_symbol(row) if row else None

    @staticmethod
    def _row_to_symbol(row: tuple) -> SymbolHit:
        name, qualname, kind, file, line, parents_csv, docstring = row
        parents = tuple(p for p in parents_csv.split(",") if p) if parents_csv else ()
        return SymbolHit(
            name=name,
            qualname=qualname,
            kind=kind,
            file=file,
            line=line,
            parents=parents,
            docstring=docstring or None,
            citation_id=f"{file}:{line}",
        )

    # ------------------------------------------------------------------
    # Text retrieval (TF-IDF)
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        symbol_limit: int = 5,
        text_limit: int = 5,
        worked_example_limit: int = 3,
    ) -> RetrievalResult:
        symbols: list[SymbolHit] = []
        # Heuristic: if the query is one or two CamelCase / snake_case
        # tokens, treat it as a symbol lookup too.
        tokens = _tokenize(query)
        if len(tokens) <= 2:
            for tok in tokens[:1]:
                symbols.extend(self.find_symbol(tok)[:symbol_limit])
        text = self._retrieve_text(query, text_limit)
        examples = self._retrieve_worked_examples(query, worked_example_limit)
        return RetrievalResult(
            symbols=symbols[:symbol_limit],
            text=text,
            worked_examples=examples,
        )

    def _retrieve_text(self, query: str, limit: int) -> list[TextHit]:
        # Drop stopwords from the query so a short narrow question
        # ("In the 1997 movie Ovosodo, where does Tommaso move?") doesn't
        # score every chunk on "in/the/movie/where" before the rare
        # signal token ("Ovosodo") gets a chance to dominate. Index
        # side keeps stopwords for accurate IDF accounting.
        q_tokens = _tokenize_query(query)
        if not q_tokens:
            # Fall back to full token set if stopword filter ate the
            # whole query (paranoid: query might be all stopwords).
            q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        with self._connect() as cx:
            n_row = cx.execute(
                "SELECT value FROM meta WHERE key = 'N'"
            ).fetchone()
            if not n_row:
                return []
            n = int(n_row[0])
            if n == 0:
                return []

            # Pull df for query tokens in one shot.
            placeholders = ",".join("?" for _ in q_tokens)
            df_rows = cx.execute(
                f"SELECT term, df FROM text_df WHERE term IN ({placeholders})",
                tuple(q_tokens),
            ).fetchall()
            df = {term: cnt for term, cnt in df_rows}
            if not df:
                return []

            # Compute idf per query term.
            q_idf = {
                term: math.log((n + 1) / (df.get(term, 0) + 1)) + 1.0
                for term in q_tokens
            }

            # Candidate chunks: any chunk that contains any query term.
            cand_rows = cx.execute(
                f"""SELECT DISTINCT chunk_id FROM text_terms
                    WHERE term IN ({placeholders})""",
                tuple(q_tokens),
            ).fetchall()
            cand_ids = [r[0] for r in cand_rows]
            if not cand_ids:
                return []

            # Score each candidate by sum(tf * idf) over query terms.
            # For modest corpora (<100K chunks) this is fine; switch to
            # a single-shot SQL accumulator at v1 if profile says so.
            scored: list[tuple[int, float]] = []
            for cid in cand_ids:
                rows = cx.execute(
                    """SELECT term, tf FROM text_terms WHERE chunk_id = ?""",
                    (cid,),
                ).fetchall()
                tf_map = {term: tf for term, tf in rows}
                score = sum(
                    tf_map.get(term, 0) * q_idf.get(term, 0.0)
                    for term in q_tokens
                )
                if score > 0:
                    scored.append((cid, score))

            scored.sort(key=lambda x: x[1], reverse=True)
            top = scored[:limit]
            if not top:
                return []

            ids_clause = ",".join("?" for _ in top)
            chunk_rows = cx.execute(
                f"""SELECT id, file, start_line, end_line, content
                    FROM text_chunks WHERE id IN ({ids_clause})""",
                tuple(cid for cid, _ in top),
            ).fetchall()

        score_by_id = dict(top)
        chunk_by_id = {r[0]: r for r in chunk_rows}
        out: list[TextHit] = []
        for cid, score in top:
            row = chunk_by_id.get(cid)
            if row is None:
                continue
            _, file, start_line, end_line, content = row
            out.append(TextHit(
                content=content,
                file=file,
                start_line=start_line,
                end_line=end_line,
                score=float(score),
                citation_id=f"{file}:{start_line}-{end_line}",
            ))
        return out

    # ------------------------------------------------------------------
    # Worked examples ("second brain")
    # ------------------------------------------------------------------

    def ingest_worked_example(
        self,
        *,
        problem_text: str,
        reasoning_steps: list[str],
        final_answer: str,
        category: str = "",
        subject: str = "",
        source: str = "manual",
    ) -> int:
        """Insert a single worked example. Returns the row id (or the
        existing id if (problem_text, source) already in the table).

        TF-IDF tokens are computed from `problem_text` only — the
        reasoning chain isn't searched, since we want similarity on the
        problem statement, not on the answer text."""
        if not problem_text.strip():
            raise ValueError("problem_text cannot be empty")
        if not isinstance(reasoning_steps, list):
            raise TypeError("reasoning_steps must be a list[str]")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as cx:
            cur = cx.execute(
                """INSERT OR IGNORE INTO worked_examples
                   (problem_text, category, subject, reasoning_json,
                    final_answer, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    problem_text,
                    category,
                    subject,
                    json.dumps(list(reasoning_steps)),
                    final_answer,
                    source,
                    now,
                ),
            )
            if cur.rowcount == 0:
                # Already present — return the existing id.
                row = cx.execute(
                    "SELECT id FROM worked_examples WHERE problem_text = ? AND source = ?",
                    (problem_text, source),
                ).fetchone()
                return int(row[0]) if row else -1
            example_id = int(cur.lastrowid)
            tokens = _tokenize(problem_text)
            term_freq = Counter(tokens)
            for term, tf in term_freq.items():
                cx.execute(
                    """INSERT INTO worked_example_terms
                       (example_id, term, tf) VALUES (?, ?, ?)""",
                    (example_id, term, tf),
                )
            self._recompute_worked_df(cx)
        return example_id

    def _recompute_worked_df(self, cx: sqlite3.Connection) -> None:
        cx.execute("DELETE FROM worked_example_df")
        cx.execute(
            """INSERT INTO worked_example_df(term, df)
               SELECT term, COUNT(DISTINCT example_id) FROM worked_example_terms
               GROUP BY term"""
        )

    def list_worked_examples(self, *, limit: int = 50) -> list[WorkedExampleHit]:
        """Return all worked examples (most recent first), with score=0.0."""
        with self._connect() as cx:
            rows = cx.execute(
                """SELECT problem_text, category, subject, reasoning_json,
                          final_answer, source, id
                   FROM worked_examples
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [self._row_to_worked_example(r, score=0.0) for r in rows]

    def _retrieve_worked_examples(
        self, query: str, limit: int
    ) -> list[WorkedExampleHit]:
        if limit <= 0:
            return []
        q_tokens = _tokenize_query(query)
        if not q_tokens:
            q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        with self._connect() as cx:
            n_row = cx.execute(
                "SELECT COUNT(*) FROM worked_examples"
            ).fetchone()
            n = int(n_row[0]) if n_row else 0
            if n == 0:
                return []

            placeholders = ",".join("?" for _ in q_tokens)
            df_rows = cx.execute(
                f"SELECT term, df FROM worked_example_df WHERE term IN ({placeholders})",
                tuple(q_tokens),
            ).fetchall()
            df = {term: cnt for term, cnt in df_rows}
            if not df:
                return []

            q_idf = {
                term: math.log((n + 1) / (df.get(term, 0) + 1)) + 1.0
                for term in q_tokens
            }

            cand_rows = cx.execute(
                f"""SELECT DISTINCT example_id FROM worked_example_terms
                    WHERE term IN ({placeholders})""",
                tuple(q_tokens),
            ).fetchall()
            cand_ids = [r[0] for r in cand_rows]
            if not cand_ids:
                return []

            scored: list[tuple[int, float]] = []
            for eid in cand_ids:
                rows = cx.execute(
                    "SELECT term, tf FROM worked_example_terms WHERE example_id = ?",
                    (eid,),
                ).fetchall()
                tf_map = {term: tf for term, tf in rows}
                score = sum(
                    tf_map.get(term, 0) * q_idf.get(term, 0.0)
                    for term in q_tokens
                )
                if score > 0:
                    scored.append((eid, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            top = scored[:limit]
            if not top:
                return []

            ids_clause = ",".join("?" for _ in top)
            rows = cx.execute(
                f"""SELECT problem_text, category, subject, reasoning_json,
                           final_answer, source, id
                    FROM worked_examples WHERE id IN ({ids_clause})""",
                tuple(eid for eid, _ in top),
            ).fetchall()

        score_by_id = dict(top)
        out: list[WorkedExampleHit] = []
        for r in rows:
            eid = int(r[6])
            out.append(self._row_to_worked_example(r, score=score_by_id.get(eid, 0.0)))
        # Re-sort because the IN clause doesn't preserve scoring order.
        out.sort(key=lambda h: h.score, reverse=True)
        return out

    @staticmethod
    def _row_to_worked_example(row: tuple, *, score: float) -> WorkedExampleHit:
        problem_text, category, subject, reasoning_json, final_answer, source, eid = row
        try:
            steps = tuple(json.loads(reasoning_json))
        except (json.JSONDecodeError, TypeError):
            steps = ()
        return WorkedExampleHit(
            problem_text=problem_text,
            category=category or "",
            subject=subject or "",
            reasoning_steps=steps,
            final_answer=final_answer or "",
            source=source or "manual",
            score=float(score),
            citation_id=f"worked_example:{eid}",
        )

    # ------------------------------------------------------------------
    # Stats / admin
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._connect() as cx:
            symbols = cx.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            chunks = cx.execute("SELECT COUNT(*) FROM text_chunks").fetchone()[0]
            terms = cx.execute("SELECT COUNT(*) FROM text_df").fetchone()[0]
            worked = cx.execute(
                "SELECT COUNT(*) FROM worked_examples"
            ).fetchone()[0]
        return {
            "symbols": symbols,
            "chunks": chunks,
            "unique_terms": terms,
            "worked_examples": worked,
        }
