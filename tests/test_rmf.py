"""RMF: NIST 800-53 catalog flatten + ingest. Uses a tiny synthetic OSCAL catalog
so the test is offline + fast (no 10 MB download)."""
from __future__ import annotations

import json

from drydock import rmf, graphrag, skills

_CATALOG = {"catalog": {"groups": [{
    "id": "ac", "title": "Access Control", "controls": [
        {"id": "ac-2", "title": "Account Management",
         "params": [{"id": "ac-2_prm_1", "label": "organization-defined roles"}],
         "parts": [
             {"name": "statement", "prose": "Manage accounts for {{ insert: param, ac-2_prm_1 }}."},
             {"name": "assessment-objective", "prose": "Determine if accounts are managed."},
         ],
         "controls": [
             {"id": "ac-2.1", "title": "Automated Account Management",
              "parts": [{"name": "statement", "prose": "Support account management with automation."}]},
         ]},
    ]}]}}


def test_flatten_resolves_params_and_parts(tmp_path):
    docs = rmf.catalog_to_family_docs(_CATALOG, tmp_path)
    text = docs[0].read_text()
    assert "AC-2 — Account Management" in text
    assert "[organization-defined roles]" in text          # param resolved
    assert "Assessment objective:" in text
    assert "AC-2.1 — Automated Account Management" in text  # enhancement included


def test_family_filter(tmp_path):
    assert rmf.catalog_to_family_docs(_CATALOG, tmp_path, families=["si"]) == []  # no SI in catalog
    assert len(rmf.catalog_to_family_docs(_CATALOG, tmp_path, families=["ac"])) == 1


def test_bootstrap_from_local_source_ingests_into_kb(tmp_path):
    src = tmp_path / "cat.json"; src.write_text(json.dumps(_CATALOG))
    stats = rmf.bootstrap(str(tmp_path), source=str(src))
    assert stats["family_docs"] == 1 and stats["chunks"] >= 1
    idx = graphrag.load_index(graphrag.default_store_path(str(tmp_path)))
    assert idx is not None
    res = graphrag.query_index(idx, "account management automation")
    assert res["chunks"] and "ac" in res["chunks"][0]["source"].lower()


def test_rmf_skills_are_bundled():
    sk = skills.load_skills("/tmp")
    for name in ("rmf-control", "rmf-categorize", "rmf-review", "rmf-poam"):
        assert name in sk and sk[name].body.strip()
