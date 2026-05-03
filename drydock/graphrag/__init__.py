"""GraphRAG — persistent context module for Drydock Sovereign v2.

A first-class deployable module per the SOVEREIGN_PRD. Phase-2 deliverable.

This first cut focuses on the two highest-volume failure shapes from
TRIAGE_v1.md:

1. **Code graph** — symbol → definition site lookup, including parent-class
   chains across packages. Mitigates pattern 4 (inheritance blindness:
   model reads `flask/wrappers.py` 13× looking for `is_json` when the
   answer is in werkzeug's parent class).

2. **Project memory** — chunked markdown / text retrieval with citations.
   Mitigates pattern 10c (multi-module rewrites exceed working-memory
   budget) by giving the model a way to recall PRD goals and per-module
   contracts across sessions.

Local-only by design: SQLite storage, stdlib + scikit-learn for TF-IDF
(swap-in for embeddings is a v1 concern). No remote services.

Public surface:
    from drydock.graphrag import Index, retrieve_symbols, retrieve_text
    from drydock.graphrag.retriever import Retriever, RetrievalResult

CLI entry point:
    python -m drydock.graphrag ingest <path>
    python -m drydock.graphrag query <text>
    python -m drydock.graphrag symbols <name>
"""
from __future__ import annotations

from drydock.graphrag.retriever import (
    RetrievalResult,
    Retriever,
    SymbolHit,
    TextHit,
)
from drydock.graphrag.storage import Index

__all__ = [
    "Index",
    "Retriever",
    "RetrievalResult",
    "SymbolHit",
    "TextHit",
]
