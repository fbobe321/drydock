"""GraphRAG SQLite+FTS5 backend: scalable store that queries only matching rows
(no full-file load), with legacy-JSON migration."""
from __future__ import annotations

import os
import tempfile

from drydock import graphrag as g


def _docs():
    d = tempfile.mkdtemp()
    docs = os.path.join(d, "docs"); os.makedirs(docs)
    open(os.path.join(docs, "a.md"), "w").write(
        "# Auth\nThe TokenValidator checks JWT expiry. Refunds via the BillingService.")
    open(os.path.join(docs, "b.md"), "w").write(
        "# Billing\nThe BillingService issues refunds within 30 days.")
    return d, docs


def test_default_store_is_sqlite():
    assert str(g.default_store_path("/x")).endswith(".db")


def test_build_query_sqlite():
    d, docs = _docs()
    store = os.path.join(d, ".drydock", "graphrag.db")
    stats = g.build_index([docs], store, cwd=d)
    assert stats["chunks"] == 2 and os.path.exists(store)
    idx = g.load_index(store)
    assert idx == {"_db": store}                     # lazy handle, not the data
    r = g.query_index(idx, "how are refunds handled", k=3)
    assert r["chunks"] and any("refund" in c["text"].lower() for c in r["chunks"])
    assert g.sources(idx) == ["docs/a.md", "docs/b.md"]
    assert g.index_stats(idx)["chunks"] == 2


def test_add_to_sqlite_appends():
    d, docs = _docs()
    store = os.path.join(d, ".drydock", "graphrag.db")
    g.build_index([docs], store, cwd=d)
    open(os.path.join(docs, "c.md"), "w").write("# Refund policy\nPartial refunds allowed after 30 days.")
    s2 = g.add_to_index([docs], store, cwd=d)
    assert s2["files"] == 1 and s2["chunks"] == 3
    r = g.query_index(g.load_index(store), "partial refund policy", k=3)
    assert any(c["source"] == "docs/c.md" for c in r["chunks"])


def test_migrate_json_to_sqlite():
    d, docs = _docs()
    js = os.path.join(d, "old.json")
    g.build_index([docs], js, cwd=d)                 # explicit .json → legacy format
    assert os.path.getsize(js) > 0
    db = os.path.join(d, "new.db")
    m = g.migrate_json_to_sqlite(js, db)
    assert m["chunks"] == 2 and os.path.exists(db)
    r = g.query_index(g.load_index(db), "refunds", k=2)
    assert r["chunks"]


def test_resolve_prefers_db_then_json():
    d, docs = _docs()
    base = os.path.join(d, ".drydock", "graphrag.db")
    g.build_index([docs], base, cwd=d)
    # asking for the .json path resolves to the existing .db
    assert str(g._resolve_store(base.replace(".db", ".json"))).endswith(".db")


def test_missing_store_returns_none():
    assert g.load_index("/no/such/graphrag.db") is None
