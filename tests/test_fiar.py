"""FIAR audit-readiness engine — engagement model, seed library, tools, CLI."""
from __future__ import annotations

import json

from drydock import fiar
from drydock.tools import (
    tool_fiarcontrols, tool_fiarcontrol, tool_fiarassess,
    tool_fiarfinding, tool_fiarreconcile,
)


# ── vocabularies ──────────────────────────────────────────────────────────
def test_five_distinct_assertions():
    # The guidance lists five commonly-accepted assertions, kept distinct.
    assert set(fiar.ASSERTIONS) == {"E", "C", "V", "PD", "RO"}


def test_canonical_assertion_aliases():
    assert fiar.canonical_assertion("existence") == "E"
    assert fiar.canonical_assertion("Rights & Obligations") == "RO"
    assert fiar.canonical_assertion("valuation") == "V"
    assert fiar.canonical_assertion("PD") == "PD"


def test_canonical_status_maps_pass_fail():
    assert fiar.canonical_status("pass") == "effective"
    assert fiar.canonical_status("FAILED") == "deficient"
    assert fiar.canonical_status("n/a") == "not_applicable"
    assert fiar.canonical_status(None) == "not_tested"
    assert fiar.canonical_status("garbage") == "not_tested"


# ── seed library / engagement ─────────────────────────────────────────────
def test_new_engagement_seeds_cycle_controls():
    eng = fiar.new_engagement("Army GF", "P2P")
    assert eng.cycle == "Procure-to-Pay"
    ids = [c.id for c in eng.controls]
    assert ids == ["P2P-01", "P2P-02", "P2P-03"]
    # EC pairing expanded to the two distinct assertions
    p2p1 = eng.control("P2P-01")
    assert "E" in p2p1.assertions and "C" in p2p1.assertions


def test_new_engagement_all_cycles():
    eng = fiar.new_engagement("DoD-wide", "*")
    assert len(eng.controls) == sum(len(v) for v in fiar.SEED_CONTROLS.values())


def test_wave_inferred_from_cycle():
    assert fiar.new_engagement("x", "FBWT").wave == 4
    assert fiar.new_engagement("x", "P2P").wave == 2
    assert fiar.new_engagement("x", "PPE").wave == 3
    assert fiar.new_engagement("x", "P2P", wave=3).wave == 3   # explicit override


def test_itgc_controls_flagged():
    eng = fiar.new_engagement("x", "ITGC")
    assert all(c.itgc for c in eng.controls)
    assert any("segregation" in c.objective.lower() for c in eng.controls)


# ── assess / findings / readiness ─────────────────────────────────────────
def test_assess_and_readiness_rollup():
    eng = fiar.new_engagement("x", "FBWT")
    assert eng.assess("FBWT-01", status="effective", evidence="Nov recon reviewed", sample_size=1)
    assert eng.assess("FBWT-02", status="deficient", evidence="aged items unresolved",
                      sample_size=25, exceptions=4)
    assert not eng.assess("NOPE-99", status="effective")
    r = eng.readiness()
    assert r["counts"]["effective"] == 1 and r["counts"]["deficient"] == 1
    assert r["pct_tested"] == 100.0        # both applicable controls tested
    assert r["pct_effective"] == 50.0


def test_add_finding_and_cap_autonumber_and_severity():
    eng = fiar.new_engagement("x", "FBWT")
    f = eng.add_finding(control_id="FBWT-01", condition="no recon", severity="bogus")
    assert f.id == "NFR-001"
    assert f.severity == "deficiency"      # invalid severity falls back
    f2 = eng.add_finding(control_id="FBWT-02", condition="aged items",
                         severity="material_weakness")
    assert f2.id == "NFR-002" and f2.severity == "material_weakness"
    cap = eng.add_cap(finding_id="NFR-002", action="fix recon",
                      milestones=[{"desc": "SOP", "done": True}, {"desc": "retest", "done": False}])
    assert cap.id == "CAP-001"
    r = eng.readiness()
    assert r["open_material_weaknesses"] == 1 and r["open_caps"] == 1


def test_roundtrip_json(tmp_path):
    eng = fiar.new_engagement("Navy WCF", "P2P")
    eng.assess("P2P-01", status="effective", evidence="traced 30 obligations")
    eng.add_finding(control_id="P2P-02", condition="missing receiving reports")
    p = tmp_path / "eng.json"
    eng.save(p)
    eng2 = fiar.Engagement.load(p)
    assert eng2.name == "Navy WCF"
    assert eng2.control("P2P-01").status == "effective"
    assert len(eng2.findings) == 1
    # the file is valid, human-readable JSON
    data = json.loads(p.read_text())
    assert data["assessable_unit"] == "Procure-to-Pay"


# ── reconciliation ────────────────────────────────────────────────────────
def test_reconcile():
    assert fiar.reconcile(100.0, 100.0)["reconciled"] is True
    r = fiar.reconcile(1000.0, 999.0, tolerance=0.5)
    assert r["difference"] == 1.0 and r["reconciled"] is False
    assert fiar.reconcile(1000.0, 999.0, tolerance=2.0)["reconciled"] is True


# ── tools ─────────────────────────────────────────────────────────────────
def _mk(tmp_path, cycle="FBWT"):
    eng = fiar.new_engagement("Test Eng", cycle)
    p = tmp_path / "e.json"
    eng.save(p)
    return str(p)


def test_tool_fiarcontrols_and_filter(tmp_path):
    p = _mk(tmp_path)
    out = tool_fiarcontrols({"path": p}, {})
    assert "Wave 4" in out and "FBWT-01" in out
    assert "not_tested=2" in out
    tool_fiarassess({"path": p, "id": "FBWT-01", "status": "deficient"}, {})
    only_def = tool_fiarcontrols({"path": p, "status": "deficient"}, {})
    assert "FBWT-01" in only_def and "FBWT-02" not in only_def


def test_tool_fiarcontrol_detail(tmp_path):
    p = _mk(tmp_path)
    out = tool_fiarcontrol({"path": p, "id": "FBWT-01"}, {})
    assert "Existence" in out and "Reconciliation workpaper" in out
    assert tool_fiarcontrol({"path": p, "id": "ZZZ"}, {}).startswith("No control")


def test_tool_fiarassess_nudges_finding_on_deficient(tmp_path):
    p = _mk(tmp_path)
    out = tool_fiarassess({"path": p, "id": "FBWT-01", "status": "deficient",
                           "evidence": "no Nov recon", "sample_size": 1, "exceptions": 1}, {})
    assert "deficient" in out and "FiarFinding" in out
    assert fiar.Engagement.load(p).control("FBWT-01").exceptions == 1


def test_tool_fiarfinding_requires_condition(tmp_path):
    p = _mk(tmp_path)
    assert "condition" in tool_fiarfinding({"path": p}, {}).lower()
    ok = tool_fiarfinding({"path": p, "control_id": "FBWT-01",
                           "condition": "recon not performed",
                           "severity": "significant_deficiency"}, {})
    assert "NFR-001" in ok
    assert fiar.Engagement.load(p).findings[0].severity == "significant_deficiency"


def test_tool_fiarreconcile(tmp_path):
    assert "RECONCILED" in tool_fiarreconcile({"entity": 500.0, "source": 500.0}, {})
    out = tool_fiarreconcile({"entity": 500.0, "source": 400.0}, {})
    assert "OUT OF BALANCE" in out and "candidate finding" in out
    assert "numeric" in tool_fiarreconcile({"entity": "x", "source": 1}, {}).lower()


def test_tool_fiarassess_rejects_bad_status(tmp_path):
    p = _mk(tmp_path)
    assert "must be one of" in tool_fiarassess({"path": p, "id": "FBWT-01", "status": "banana"}, {})


# ── evidence chain (the differentiating harness) ──────────────────────────
def test_validate_chain():
    assert fiar.validate_chain(None)["complete"] is False
    full = {k: "x" for k in fiar.EVIDENCE_CHAIN}
    assert fiar.validate_chain(full)["complete"] is True
    partial = dict(full); partial.pop("authorization"); partial["gl_effect"] = ""
    v = fiar.validate_chain(partial)
    assert not v["complete"]
    assert set(v["missing"]) == {"authorization", "gl_effect"}


def test_effective_refused_on_incomplete_chain(tmp_path):
    p = _mk(tmp_path)
    partial = {"population": "GL FBWT detail", "sample": "item 7",
               "source_transaction": "SF1080", "supporting_document": "recon wp"}
    out = tool_fiarassess({"path": p, "id": "FBWT-01", "status": "effective",
                           "chain": partial}, {})
    assert out.startswith("REFUSED")
    assert "authorization" in out and "gl_effect" in out and "assertion" in out
    # status was NOT set to effective, but the partial chain was saved
    c = fiar.Engagement.load(p).control("FBWT-01")
    assert c.status != "effective"
    assert c.evidence_chain.get("population") == "GL FBWT detail"


def test_effective_accepted_on_complete_chain(tmp_path):
    p = _mk(tmp_path)
    full = {k: f"ev-{k}" for k in fiar.EVIDENCE_CHAIN}
    out = tool_fiarassess({"path": p, "id": "FBWT-01", "status": "effective",
                           "chain": full, "sample_size": 30, "exceptions": 0}, {})
    assert out.startswith("✓") and "effective" in out
    assert fiar.Engagement.load(p).control("FBWT-01").status == "effective"


def test_effective_without_chain_advises(tmp_path):
    p = _mk(tmp_path)
    out = tool_fiarassess({"path": p, "id": "FBWT-01", "status": "effective"}, {})
    assert out.startswith("✓") and "evidence chain" in out.lower()


def test_control_detail_shows_chain_state(tmp_path):
    p = _mk(tmp_path)
    assert "not traced" in tool_fiarcontrol({"path": p, "id": "FBWT-01"}, {})
