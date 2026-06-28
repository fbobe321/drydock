"""RMF Phase 2 — the typed ontology graph + GraphQuery/GraphAdd tools.
Inheritance reasoning is the headline: a component inherits the controls its
parent system implements (RESIDES_ON → IMPLEMENTS)."""
from __future__ import annotations

from drydock import rmf_graph as G
from drydock.tools import tool_graphquery, tool_graphadd

_CAT = {"catalog": {"groups": [
    {"id": "ac", "title": "Access Control", "controls": [
        {"id": "ac-2", "title": "Account Management",
         "parts": [{"name": "assessment-objective", "prose": "accounts are managed"}]}]},
    {"id": "pe", "title": "Physical and Environmental Protection", "controls": [
        {"id": "pe-3", "title": "Physical Access Control", "parts": []}]},
]}}


def test_build_from_catalog_typed_nodes():
    g = G.build_from_catalog(_CAT)
    assert g.get(G.control_id("ac-2"))["attrs"]["title"] == "Account Management"
    objs = g.neighbors(G.control_id("ac-2"), "ASSESSES", direction="in")
    assert objs and g.get(objs[0])["type"] == "Objective"


def test_inheritance_reasoning():
    g = G.build_from_catalog(_CAT)
    g.add_node(G.component_id("web-1"), "Component", name="web-1")
    g.add_node(G.component_id("enclave"), "Boundary", name="enclave")
    g.add_edge(G.component_id("web-1"), "RESIDES_ON", G.component_id("enclave"))
    g.add_edge(G.component_id("enclave"), "IMPLEMENTS", G.control_id("pe-3"))
    inh = g.inherited_controls(G.component_id("web-1"))
    assert G.control_id("pe-3") in inh


def test_persistence_roundtrip(tmp_path):
    g = G.build_from_catalog(_CAT)
    g.add_edge(G.component_id("a"), "RESIDES_ON", G.component_id("b"))
    p = tmp_path / "g.json"; g.save(p)
    g2 = G.RmfGraph.load(p)
    assert g2.stats()["nodes"] == g.stats()["nodes"]
    assert {"src": G.component_id("a"), "rel": "RESIDES_ON", "dst": G.component_id("b")} in g2.edges


def test_graphadd_and_graphquery_tools(tmp_path):
    from drydock import rmf_graph
    # seed a catalog graph
    g = G.build_from_catalog(_CAT)
    g.save(rmf_graph.graph_path(str(tmp_path)))
    cfg = {"cwd": str(tmp_path)}
    # build a system via the write tool
    assert "RESIDES_ON" in tool_graphadd({"op": "resides_on", "component": "srv1", "parent": "encl"}, cfg)
    assert "IMPLEMENTS" in tool_graphadd({"op": "implements", "component": "encl", "control": "PE-3"}, cfg)
    # query inheritance via the read tool
    out = tool_graphquery({"op": "inherited", "id": "srv1"}, cfg)
    assert "PE-3" in out and "inherits" in out
    # control lookup + implementers
    assert "Account Management" in tool_graphquery({"op": "control", "id": "AC-2"}, cfg)
    assert "encl" in tool_graphquery({"op": "implementers", "id": "PE-3"}, cfg)


def test_graphquery_empty_graph_is_graceful(tmp_path):
    assert "empty" in tool_graphquery({"op": "control", "id": "AC-2"}, {"cwd": str(tmp_path)}).lower()
