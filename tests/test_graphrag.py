"""GraphRAG: a local, dependency-free knowledge graph the user builds and the
agent queries via the read-only Knowledge tool."""
from __future__ import annotations

from drydock import graphrag
from drydock.tools import tool_knowledge


def _corpus(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "payments.md").write_text(
        "The Acme Payments Service authorizes a charge via the Zephyr-Key header "
        "for idempotency. Webhooks are signed with WEBHOOK_SECRET using HMAC-SHA256."
    )
    (d / "inventory.md").write_text(
        "The InventoryManager fires low-stock alerts when quantity drops below "
        "the reorder_threshold. The nightly ReorderJob emails procurement."
    )
    return d


def test_build_and_query_routes_to_right_doc(tmp_path):
    store = tmp_path / ".drydock" / "graphrag.json"
    stats = graphrag.build_index(["docs"], store, cwd=str(tmp_path))
    assert stats["files"] == 2 and stats["chunks"] >= 2 and stats["entities"] > 0

    idx = graphrag.load_index(store)
    pay = graphrag.query_index(idx, "how do I authorize a charge with idempotency?")
    assert pay["chunks"] and pay["chunks"][0]["source"].endswith("payments.md")

    inv = graphrag.query_index(idx, "low stock alerts and reorder threshold")
    assert inv["chunks"] and inv["chunks"][0]["source"].endswith("inventory.md")


def test_entities_do_not_span_newlines(tmp_path):
    store = tmp_path / ".drydock" / "graphrag.json"
    graphrag.build_index(["docs"], store, cwd=str(tmp_path))
    idx = graphrag.load_index(store)
    assert all("\n" not in e for e in idx["entities"])  # proper nouns stay on one line


def test_graph_expansion_returns_related_entities(tmp_path):
    store = tmp_path / ".drydock" / "graphrag.json"
    graphrag.build_index(["docs"], store, cwd=str(tmp_path))
    idx = graphrag.load_index(store)
    res = graphrag.query_index(idx, "ReorderJob")
    # 1-hop expansion should pull co-occurring entities from the same chunk.
    assert any("reorder_threshold" in r or "inventorymanager" in r for r in res["related"])


def test_knowledge_tool_without_index_is_graceful(tmp_path):
    out = tool_knowledge({"query": "anything"}, {"cwd": str(tmp_path)})
    assert "No knowledge base" in out and "graphrag build" in out


def test_knowledge_tool_returns_passages(tmp_path):
    store = graphrag.default_store_path(str(tmp_path))
    graphrag.build_index(["docs"], store, cwd=str(tmp_path))
    out = tool_knowledge({"query": "authorize a charge", "k": 2}, {"cwd": str(tmp_path)})
    assert "payments.md" in out and "Zephyr-Key" in out


def test_knowledge_tool_needs_a_query(tmp_path):
    assert "needs a `query`" in tool_knowledge({}, {"cwd": str(tmp_path)})


# Make the corpus available to every test via an autouse fixture.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _make_corpus(tmp_path):
    _corpus(tmp_path)
    yield
