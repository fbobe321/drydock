"""STIG .ckl/.cklb engine — parse, edit-in-place, round-trip; + the tools."""
from __future__ import annotations

import json

from drydock import stig
from drydock.tools import tool_stigrules, tool_stigrule, tool_stigset

_CKL = '''<?xml version="1.0" encoding="UTF-8"?>
<CHECKLIST><ASSET><HOST_NAME>web01</HOST_NAME><HOST_IP>10.0.0.5</HOST_IP></ASSET>
<STIGS><iSTIG><STIG_INFO></STIG_INFO>
<VULN>
<STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>V-230222</ATTRIBUTE_DATA></STIG_DATA>
<STIG_DATA><VULN_ATTRIBUTE>Rule_ID</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SV-230222r1</ATTRIBUTE_DATA></STIG_DATA>
<STIG_DATA><VULN_ATTRIBUTE>Severity</VULN_ATTRIBUTE><ATTRIBUTE_DATA>medium</ATTRIBUTE_DATA></STIG_DATA>
<STIG_DATA><VULN_ATTRIBUTE>Rule_Title</VULN_ATTRIBUTE><ATTRIBUTE_DATA>Enable FIPS</ATTRIBUTE_DATA></STIG_DATA>
<STIG_DATA><VULN_ATTRIBUTE>Check_Content</VULN_ATTRIBUTE><ATTRIBUTE_DATA>Run fips-mode-setup --check</ATTRIBUTE_DATA></STIG_DATA>
<STIG_DATA><VULN_ATTRIBUTE>Fix_Text</VULN_ATTRIBUTE><ATTRIBUTE_DATA>Run fips-mode-setup --enable</ATTRIBUTE_DATA></STIG_DATA>
<STATUS>Not_Reviewed</STATUS><FINDING_DETAILS></FINDING_DETAILS><COMMENTS></COMMENTS></VULN>
</iSTIG></STIGS></CHECKLIST>'''

_CKLB = {"title": "RHEL9", "target_data": {"host_name": "web01"}, "stigs": [{"stig_name": "RHEL 9",
    "rules": [{"group_id": "V-230222", "rule_id": "SV-230222r1", "rule_title": "Enable FIPS",
               "severity": "medium", "check_content": "fips-mode-setup --check",
               "fix_text": "fips-mode-setup --enable", "status": "not_reviewed",
               "finding_details": "", "comments": ""}]}]}


def test_ckl_roundtrip_edit_in_place(tmp_path):
    p = tmp_path / "r.ckl"; p.write_text(_CKL)
    cl = stig.load(p)
    assert cl.fmt == "ckl" and cl.asset["HOST_NAME"] == "web01"
    assert cl.rules[0].check_content == "Run fips-mode-setup --check"
    assert cl.update("V-230222", status="open", finding_details="FIPS off.", comments="by drydock")
    cl.save(p)
    re = stig.load(p)
    assert re.rules[0].status == "open" and re.rules[0].finding_details == "FIPS off."
    # other STIG_DATA preserved (schema fidelity)
    assert "Vuln_Num" in p.read_text() and "<STATUS>Open</STATUS>" in p.read_text()


def test_cklb_roundtrip(tmp_path):
    p = tmp_path / "r.cklb"; p.write_text(json.dumps(_CKLB))
    cl = stig.load(p)
    assert cl.fmt == "cklb"
    cl.update("SV-230222r1", status="not_a_finding", finding_details="FIPS on.")
    cl.save(p)
    data = json.loads(p.read_text())  # still valid JSON
    assert data["stigs"][0]["rules"][0]["status"] == "not_a_finding"
    assert stig.load(p).rules[0].finding_details == "FIPS on."


def test_status_canonicalization():
    assert stig.canonical_status("NotAFinding") == "not_a_finding"
    assert stig.canonical_status("Not_Applicable") == "not_applicable"
    assert stig.canonical_status("garbage") == "not_reviewed"


def test_stig_tools(tmp_path):
    p = tmp_path / "r.ckl"; p.write_text(_CKL); cfg = {"cwd": str(tmp_path)}
    out = tool_stigrules({"path": "r.ckl"}, cfg)
    assert "V-230222" in out and "not_reviewed=1" in out
    detail = tool_stigrule({"path": "r.ckl", "rule_id": "V-230222"}, cfg)
    assert "Check Content" in detail and "fips-mode-setup --check" in detail
    set_out = tool_stigset({"path": "r.ckl", "rule_id": "V-230222", "status": "open",
                            "finding_details": "FIPS disabled."}, cfg)
    assert "set to open" in set_out
    assert stig.load(p).rules[0].status == "open"
    # filter by status
    assert "not_reviewed=0" in tool_stigrules({"path": "r.ckl"}, cfg)


# ── edge cases (E2E PRD: malformed / incomplete checklist stubs) ────────────

def test_malformed_ckl_is_graceful(tmp_path):
    from drydock.tools import tool_stigrules
    (tmp_path / "bad.ckl").write_text('<?xml version="1.0"?><CHECKLIST><VULN><STATUS>Open')
    out = tool_stigrules({"path": "bad.ckl"}, {"cwd": str(tmp_path)})
    assert "Could not read" in out  # clean error to the model, not a crash


def test_incomplete_vuln_parses_with_defaults(tmp_path):
    (tmp_path / "inc.ckl").write_text(
        '<?xml version="1.0"?><CHECKLIST><STIGS><iSTIG>'
        '<VULN><STIG_DATA><VULN_ATTRIBUTE>Rule_ID</VULN_ATTRIBUTE>'
        '<ATTRIBUTE_DATA>SV-9</ATTRIBUTE_DATA></STIG_DATA></VULN></iSTIG></STIGS></CHECKLIST>')
    cl = stig.load(tmp_path / "inc.ckl")
    assert cl.rules[0].rule_id == "SV-9" and cl.rules[0].status == "not_reviewed"


def test_empty_and_minimal_checklists(tmp_path):
    (tmp_path / "e.ckl").write_text('<?xml version="1.0"?><CHECKLIST><STIGS></STIGS></CHECKLIST>')
    assert stig.load(tmp_path / "e.ckl").rules == []
    (tmp_path / "m.cklb").write_text('{"stigs":[{"rules":[{"group_id":"V-5"}]}]}')
    assert stig.load(tmp_path / "m.cklb").rules[0].group_id == "V-5"


def test_cklb_writeback_only_changes_status_fields(tmp_path):
    """Editing a .cklb must change ONLY status/finding_details/comments and keep
    everything else byte-stable (eMASS round-trip fidelity)."""
    import json
    data = {"title": "t", "stigs": [{"stig_name": "Demo", "version": "1", "rules": [
        {"group_id": "V-1", "rule_id": "SV-1", "rule_title": "Rule one",
         "severity": "high", "status": "not_reviewed", "check_content": "do x",
         "fix_text": "fix x", "finding_details": "", "comments": "", "weight": "10.0"}]}]}
    p = tmp_path / "a.cklb"; p.write_text(json.dumps(data))
    cl = stig.load(p)
    cl.update("V-1", status="open", finding_details="found it")
    cl.save(p)
    re = json.loads(p.read_text())
    rule = re["stigs"][0]["rules"][0]
    assert rule["status"] == "open" and rule["finding_details"] == "found it"
    # untouched fields preserved exactly
    assert rule["severity"] == "high" and rule["check_content"] == "do x"
    assert rule["weight"] == "10.0" and rule["rule_title"] == "Rule one"


def test_summary_lines_scale_and_no_silent_truncation(tmp_path):
    import json
    # 60 rules: 55 open, 5 not_reviewed → exercises >40 hint, open hint, >50 cap note
    rules = [{"group_id": f"V-{i}", "rule_id": f"SV-{i}", "rule_title": f"r{i}",
              "severity": "medium", "status": "open" if i < 55 else "not_reviewed"}
             for i in range(60)]
    (tmp_path / "b.cklb").write_text(json.dumps(
        {"stigs": [{"stig_name": "S", "version": "1", "rules": rules}]}))
    cl = stig.load(tmp_path / "b.cklb")
    summ = "\n".join(stig.summary_lines(cl, "b.cklb"))
    assert "/loop 5 /stig-assess b.cklb" in summ          # exact not_reviewed count
    assert "List open findings" in summ                    # open hint shown
    open_view = "\n".join(stig.summary_lines(cl, "b.cklb", "open"))
    assert "showing first 50 of 55" in open_view           # no silent truncation
    assert open_view.count("V-") <= 51                     # capped list + the note


def test_summary_lines_large_checklist_hint(tmp_path):
    import json
    rules = [{"group_id": f"V-{i}", "rule_id": f"SV-{i}", "rule_title": "x",
              "severity": "low", "status": "not_reviewed"} for i in range(286)]
    (tmp_path / "big.cklb").write_text(json.dumps(
        {"stigs": [{"stig_name": "ASD", "version": "6", "rules": rules}]}))
    cl = stig.load(tmp_path / "big.cklb")
    summ = "\n".join(stig.summary_lines(cl, "big.cklb"))
    assert "/loop 286" in summ and "286 model turns" in summ
