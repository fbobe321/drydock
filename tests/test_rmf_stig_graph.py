"""RMF Phase 2 STIG ontology — ingest a checklist into the typed graph and
trace Control <-SATISFIED_BY-> STIG-Rule."""
from __future__ import annotations

from drydock import stig, rmf_graph
from drydock.tools import tool_graphadd, tool_graphquery

_CKL = ('<?xml version="1.0"?><CHECKLIST><ASSET><HOST_NAME>web01</HOST_NAME></ASSET>'
        '<STIGS><iSTIG><STIG_INFO><SI_DATA><SID_NAME>title</SID_NAME><SID_DATA>RHEL 9 STIG</SID_DATA></SI_DATA></STIG_INFO>'
        '<VULN><STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>V-1</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>Rule_ID</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SV-1r1</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>CCI_REF</VULN_ATTRIBUTE><ATTRIBUTE_DATA>CCI-000366</ATTRIBUTE_DATA></STIG_DATA>'
        '<STATUS>Open</STATUS></VULN></iSTIG></STIGS></CHECKLIST>')


def test_parse_captures_stig_info_and_cci(tmp_path):
    p = tmp_path / "s.ckl"; p.write_text(_CKL)
    cl = stig.load(p)
    assert cl.stig_name == "RHEL 9 STIG" and cl.rules[0].cci == "CCI-000366"


def test_ingest_checklist_builds_typed_nodes(tmp_path):
    (tmp_path / "s.ckl").write_text(_CKL)
    cl = stig.load(tmp_path / "s.ckl")
    g = rmf_graph.RmfGraph()
    r = rmf_graph.ingest_checklist(g, cl)
    assert r["rules"] == 1 and r["host"] == "web01"
    rn = rmf_graph.rule_node("SV-1r1")
    assert g.get(rn)["type"] == "STIGRule"
    assert rmf_graph.stig_id("RHEL 9 STIG") in g.neighbors(rn, "PART_OF")
    assert rmf_graph.component_id("web01") in g.neighbors(rn, "EVALUATES")


def test_satisfies_links_control_to_rule(tmp_path):
    rmf_graph.RmfGraph().save(rmf_graph.graph_path(str(tmp_path)))
    cfg = {"cwd": str(tmp_path)}
    assert "SATISFIED_BY" in tool_graphadd({"op": "satisfies", "control": "CM-6", "rule": "SV-1r1"}, cfg)
    out = tool_graphquery({"op": "control", "id": "CM-6"}, cfg)
    assert "Satisfied by STIG rules" in out and "SV-1r1" in out
