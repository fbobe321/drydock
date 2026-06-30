"""CCI → NIST 800-53 control map (the STIG-rule ↔ control bridge)."""
from __future__ import annotations

from drydock import cci, rmf_graph, stig

_CCI_XML = '''<?xml version="1.0"?><cci_list><cci_items>
<cci_item id="CCI-000366"><references>
<reference title="NIST SP 800-53" version="3" index="CM-6"/>
<reference title="NIST SP 800-53A" version="1" index="CM-6.1 (ii)"/>
<reference title="NIST SP 800-53 Revision 4" version="4" index="CM-6 b"/></references></cci_item>
<cci_item id="CCI-000054"><references>
<reference title="NIST SP 800-53 Revision 5" version="5" index="AC-10"/></references></cci_item>
<cci_item id="CCI-001133"><references>
<reference title="NIST SP 800-53 Revision 4" version="4" index="SC-10 (1)"/></references></cci_item>
<cci_item id="CCI-999999"><references>
<reference title="NIST SP 800-53A" version="1" index="ZZ-9.9"/></references></cci_item>
</cci_items></cci_list>'''


def test_parse_picks_highest_rev_excludes_53A(tmp_path):
    p = tmp_path / "cci.xml"; p.write_text(_CCI_XML)
    m = cci.parse_cci_list(p)
    assert m["CCI-000366"] == "cm-6"        # rev4 base, 800-53A excluded
    assert m["CCI-000054"] == "ac-10"
    assert m["CCI-001133"] == "sc-10.1"     # enhancement normalized
    assert "CCI-999999" not in m            # only 800-53A ref → no control


def test_norm_control_formats():
    assert cci._norm_control("AC-10") == "ac-10"
    assert cci._norm_control("AC-2 (1)") == "ac-2.1"
    assert cci._norm_control("AC-2.1") == "ac-2.1"
    assert cci._norm_control("garbage") is None


def test_load_map_caches_and_is_offline_safe(tmp_path, monkeypatch):
    # no network: with no cache, returns {} (never raises)
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(cci, "fetch_to", boom)
    assert cci.load_map(str(tmp_path)) == {}
    # seed a cache → load_map returns it without fetching
    cp = cci.cache_path(str(tmp_path)); cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text('{"CCI-000366": "cm-6"}')
    assert cci.load_map(str(tmp_path)) == {"CCI-000366": "cm-6"}


def test_ingest_auto_links_via_cci(tmp_path):
    (tmp_path / "s.ckl").write_text(
        '<?xml version="1.0"?><CHECKLIST><ASSET><HOST_NAME>h1</HOST_NAME></ASSET><STIGS><iSTIG>'
        '<STIG_INFO></STIG_INFO><VULN>'
        '<STIG_DATA><VULN_ATTRIBUTE>Rule_ID</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SV-9</ATTRIBUTE_DATA></STIG_DATA>'
        '<STIG_DATA><VULN_ATTRIBUTE>CCI_REF</VULN_ATTRIBUTE><ATTRIBUTE_DATA>CCI-000366</ATTRIBUTE_DATA></STIG_DATA>'
        '<STATUS>Open</STATUS></VULN></iSTIG></STIGS></CHECKLIST>')
    cl = stig.load(tmp_path / "s.ckl")
    g = rmf_graph.RmfGraph()
    r = rmf_graph.ingest_checklist(g, cl, {"CCI-000366": "cm-6"})
    assert r["linked"] == 1
    assert rmf_graph.rule_node("SV-9") in g.neighbors(rmf_graph.control_id("cm-6"), "SATISFIED_BY")


def test_ingest_without_map_does_not_link(tmp_path):
    (tmp_path / "s.ckl").write_text(
        '<?xml version="1.0"?><CHECKLIST><STIGS><iSTIG><STIG_INFO></STIG_INFO><VULN>'
        '<STIG_DATA><VULN_ATTRIBUTE>Rule_ID</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SV-9</ATTRIBUTE_DATA></STIG_DATA>'
        '<STATUS>Open</STATUS></VULN></iSTIG></STIGS></CHECKLIST>')
    cl = stig.load(tmp_path / "s.ckl")
    r = rmf_graph.ingest_checklist(rmf_graph.RmfGraph(), cl)
    assert r["linked"] == 0


def test_load_map_creates_dir_and_caches(tmp_path, monkeypatch):
    """Regression: the fetch must create .drydock/rmf/ before writing — a real
    fetch failed because the dir didn't exist yet (found via hands-on TUI test)."""
    def fake_fetch(path, **kw):
        # a real fetch writes to .drydock/rmf/cci_map.xml — the dir must exist
        from pathlib import Path
        Path(path).write_text(_CCI_XML)
    monkeypatch.setattr(cci, "fetch_to", fake_fetch)
    m = cci.load_map(str(tmp_path))
    assert m["CCI-000366"] == "cm-6"                 # parsed from the "fetched" list
    assert cci.cache_path(str(tmp_path)).exists()    # cached as json


def test_parse_cci_tolerates_malformed_items(tmp_path):
    # items with no references, empty references, and non-NIST refs are skipped,
    # not crashed on
    xml = ('<?xml version="1.0"?><cci_list><cci_items>'
           '<cci_item id="CCI-A"></cci_item>'                       # no references
           '<cci_item id="CCI-B"><references></references></cci_item>'
           '<cci_item id="CCI-C"><references>'
           '<reference title="Some Other Doc" version="1" index="X-9"/></references></cci_item>'
           '<cci_item id="CCI-D"><references>'
           '<reference title="NIST SP 800-53 Revision 5" version="5" index="SI-4"/></references></cci_item>'
           '</cci_items></cci_list>')
    p = tmp_path / "c.xml"; p.write_text(xml)
    m = cci.parse_cci_list(p)
    assert m == {"CCI-D": "si-4"}                            # only the valid mapping
