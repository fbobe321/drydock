"""eMASS POA&M CSV export from open STIG findings (deterministic)."""
from __future__ import annotations

import csv

from drydock import poam, stig

_CKL = ('<?xml version="1.0"?><CHECKLIST><ASSET><HOST_NAME>h1</HOST_NAME></ASSET><STIGS><iSTIG><STIG_INFO></STIG_INFO>'
        '<VULN><STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>V-1</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>Rule_ID</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SV-1</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>Rule_Title</VULN_ATTRIBUTE><ATTRIBUTE_DATA>Disable debug</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>Severity</VULN_ATTRIBUTE><ATTRIBUTE_DATA>high</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>Fix_Text</VULN_ATTRIBUTE><ATTRIBUTE_DATA>Set debug=false.</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>CCI_REF</VULN_ATTRIBUTE><ATTRIBUTE_DATA>CCI-000366</ATTRIBUTE_DATA></STIG_DATA>'
        '<STATUS>Open</STATUS></VULN>'
        '<VULN><STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>V-2</ATTRIBUTE_DATA></STIG_DATA>'
        '<STATUS>NotAFinding</STATUS></VULN></iSTIG></STIGS></CHECKLIST>')


def test_poam_rows_only_open_with_control_and_severity(tmp_path):
    p = tmp_path / "s.ckl"; p.write_text(_CKL)
    cl = stig.load(p)
    rows = poam.poam_rows(cl, {"CCI-000366": "cm-6"})
    assert len(rows) == 1                                   # only the Open finding
    row = rows[0]
    assert row["Control"] == "CM-6"                         # from the CCI map
    assert row["Severity"] == "High" and row["Raw Severity"] == "CAT I"   # high → CAT I/High
    assert row["POA&M Status"] == "Ongoing"
    assert "Set debug=false." in row["Milestone Description"]
    assert "V-1" in row["Vulnerability Description"]


def test_poam_unmapped_cci_is_marked_not_dropped(tmp_path):
    p = tmp_path / "s.ckl"; p.write_text(_CKL)
    cl = stig.load(p)
    rows = poam.poam_rows(cl, {})                           # no map
    assert rows[0]["Control"] == "(unmapped CCI-000366)"    # marked, not silently lost


def test_write_csv_has_emass_headers(tmp_path):
    p = tmp_path / "s.ckl"; p.write_text(_CKL)
    cl = stig.load(p)
    out = tmp_path / "poam.csv"
    n = poam.export(cl, {"CCI-000366": "cm-6"}, out)["rows"]
    assert n == 1
    with open(out, newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == poam.EMASS_HEADERS
        row = next(reader)
        assert row["Control"] == "CM-6" and row["Severity"] == "High"


def test_poam_cklb_mixed_severities(tmp_path):
    import json
    data = {"stigs": [{"stig_name": "S", "version": "1", "rules": [
        {"group_id": "V-10", "rule_id": "SV-10", "rule_title": "low one", "severity": "low",
         "status": "open", "fix_text": "fix low", "ccis": ["CCI-1"]},
        {"group_id": "V-11", "rule_id": "SV-11", "rule_title": "med one", "severity": "medium",
         "status": "open", "fix_text": "fix med"},
        {"group_id": "V-12", "rule_id": "SV-12", "rule_title": "clean", "severity": "high",
         "status": "not_a_finding"},
    ]}]}
    p = tmp_path / "m.cklb"; p.write_text(json.dumps(data))
    cl = stig.load(p)
    rows = poam.poam_rows(cl, {"CCI-1": "ac-2"})
    assert len(rows) == 2                                  # 2 open, the NAF excluded
    by_sev = {r["Source Identifying Vulnerability"]: r["Severity"] for r in rows}
    assert by_sev["SV-10"] == "Low" and by_sev["SV-11"] == "Moderate"
    assert rows[0]["Control"] == "AC-2"                    # CCI-1 mapped
    assert rows[1]["Control"] == "(no CCI)"                # no cci on V-11


def test_poam_empty_checklist_writes_header_only(tmp_path):
    import json
    p = tmp_path / "e.cklb"; p.write_text(json.dumps({"stigs": [{"rules": []}]}))
    cl = stig.load(p)
    out = tmp_path / "e.csv"
    assert poam.export(cl, {}, out)["rows"] == 0
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("Control,")   # header only, valid CSV
