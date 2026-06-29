"""XCCDF benchmark (raw STIG) -> blank .ckl generator — the inverse of STIG
Viewer, closing the loop: STIG benchmark -> assess -> completed .ckl."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from drydock import stig

# A minimal but real-shaped DISA STIG XCCDF (namespaced, like the live files).
_XCCDF = '''<?xml version="1.0"?>
<Benchmark xmlns="http://checklists.nist.gov/xccdf/1.1" id="ASD_STIG">
  <title>Application Security and Development STIG</title>
  <version>6</version>
  <plain-text id="release-info">Release: 1 Benchmark Date: 24 Jan 2024</plain-text>
  <Group id="V-222387">
    <title>SRG-APP-000001</title>
    <Rule id="SV-222387r879511_rule" severity="medium" weight="10.0">
      <version>APSC-DV-000010</version>
      <title>The application must limit the number of concurrent sessions.</title>
      <description>&lt;VulnDiscussion&gt;Limiting sessions reduces risk.&lt;/VulnDiscussion&gt;&lt;FalsePositives&gt;&lt;/FalsePositives&gt;</description>
      <ident system="http://cyber.mil/cci">CCI-000054</ident>
      <fixtext fixref="F-1">Configure the application to limit concurrent sessions.</fixtext>
      <check system="C-1"><check-content>Review the application configuration for a session limit.</check-content></check>
    </Rule>
  </Group>
</Benchmark>'''


def test_parse_xccdf(tmp_path):
    p = tmp_path / "b-xccdf.xml"; p.write_text(_XCCDF)
    b = stig.parse_xccdf(p)
    assert b["title"].startswith("Application Security") and b["version"] == "6"
    assert "Release: 1" in b["release"]
    r = b["rules"][0]
    assert r["group_id"] == "V-222387" and r["rule_id"] == "SV-222387r879511_rule"
    assert r["rule_ver"] == "APSC-DV-000010" and r["severity"] == "medium"
    assert r["discussion"] == "Limiting sessions reduces risk."   # VulnDiscussion extracted
    assert "session limit" in r["check"] and "concurrent sessions" in r["fix"]
    assert r["ccis"] == ["CCI-000054"]


def test_xccdf_to_blank_ckl(tmp_path):
    p = tmp_path / "b-xccdf.xml"; p.write_text(_XCCDF)
    cl = stig.xccdf_to_checklist(p, host="appsrv1")
    assert len(cl.rules) == 1 and cl.counts()["not_reviewed"] == 1
    assert cl.rules[0].rule_id == "SV-222387r879511_rule" and cl.rules[0].cci == "CCI-000054"
    out = tmp_path / "gen.ckl"; cl.save(out)
    ET.parse(out)  # valid XML
    # round-trips through the engine and is assessable
    re = stig.load(out)
    assert re.rules[0].check_content.startswith("Review the application")
    assert re.asset["HOST_NAME"] == "appsrv1"
    re.update("V-222387", status="open", finding_details="no limit configured"); re.save(out)
    assert stig.load(out).rules[0].status == "open"
    # the regenerated .ckl preserves the DISA status enum
    assert "<STATUS>Open</STATUS>" in out.read_text()
