"""Connected E2E tests (RMF E2E PRD). The DETERMINISTIC + integration parts:
schema fidelity, and network-resilience fallback. The LIVE upstream fetch is
opt-in (set DRYDOCK_E2E_NETWORK=1) so the normal suite stays fast + offline-safe.
Model-accuracy suites (assessment/remediation correctness) are verified hands-on
in the TUI, not here (see the operator's TUI-only rule)."""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET

import pytest

from drydock import rmf, rmf_graph, stig

_CAT = {"catalog": {"groups": [{"id": "ac", "title": "Access Control", "controls": [
    {"id": "ac-2", "title": "Account Management",
     "parts": [{"name": "statement", "prose": "Manage accounts."}]}]}]}}


def test_network_resilience_falls_back_to_cache(tmp_path, monkeypatch):
    # Seed a cached catalog, then make upstream fetch fail on refresh.
    base = rmf.rmf_dir(str(tmp_path)); base.mkdir(parents=True)
    (base / "catalog.json").write_text(json.dumps(_CAT))

    def boom(*a, **k):
        raise OSError("upstream unreachable")
    monkeypatch.setattr(rmf, "fetch_catalog", boom)
    # refresh=True tries upstream, fails, but falls back to the cache → no crash
    stats = rmf.bootstrap(str(tmp_path), refresh=True)
    assert stats["family_docs"] == 1 and stats["chunks"] >= 1


def test_first_bootstrap_offline_with_no_cache_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(rmf, "fetch_catalog", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(OSError):
        rmf.bootstrap(str(tmp_path))  # nothing cached to fall back to


def test_ckl_regenerates_wellformed_and_status_enum(tmp_path):
    p = tmp_path / "r.ckl"
    p.write_text('<?xml version="1.0"?><CHECKLIST><ASSET><HOST_NAME>h</HOST_NAME></ASSET>'
                 '<STIGS><iSTIG><STIG_INFO></STIG_INFO><VULN>'
                 '<STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>V-1</ATTRIBUTE_DATA></STIG_DATA>'
                 '<STIG_DATA><VULN_ATTRIBUTE>Rule_ID</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SV-1</ATTRIBUTE_DATA></STIG_DATA>'
                 '<STATUS>Not_Reviewed</STATUS><FINDING_DETAILS></FINDING_DETAILS><COMMENTS></COMMENTS>'
                 '</VULN></iSTIG></STIGS></CHECKLIST>')
    cl = stig.load(p); cl.update("V-1", status="open", finding_details="x"); cl.save(p)
    root = ET.parse(p).getroot()                       # re-parses = well-formed
    statuses = [v.findtext("STATUS") for v in root.iter("VULN")]
    valid = {"Open", "NotAFinding", "Not_Applicable", "Not_Reviewed"}
    assert all(s in valid for s in statuses)           # DISA status enum preserved
    assert root.find(".//VULN/STIG_DATA/ATTRIBUTE_DATA").text == "V-1"  # data fidelity


def test_graph_relationship_semantics(tmp_path):
    # Suite A semantics: COMPONENT -IMPLEMENTS-> CONTROL resolves.
    g = rmf_graph.build_from_catalog(_CAT)
    g.add_edge(rmf_graph.component_id("srv"), "IMPLEMENTS", rmf_graph.control_id("ac-2"))
    assert rmf_graph.control_id("ac-2") in g.neighbors(rmf_graph.component_id("srv"), "IMPLEMENTS")


@pytest.mark.skipif(os.environ.get("DRYDOCK_E2E_NETWORK") != "1",
                    reason="live network test; set DRYDOCK_E2E_NETWORK=1 to run")
def test_live_nist_catalog_fetch_and_parse(tmp_path):
    # Suite A: pull the REAL NIST 800-53 catalog from upstream and parse it.
    cat = rmf.fetch_catalog(tmp_path / "cat.json")
    catalog = json.loads(cat.read_text())
    docs = rmf.catalog_to_family_docs(catalog, tmp_path / "out", families=["ac"])
    assert docs and "AC-2" in docs[0].read_text()
