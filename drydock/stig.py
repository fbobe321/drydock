"""STIG checklist engine — parse + generate DISA `.ckl` (XML) and `.cklb` (JSON).

These checklists carry hostnames, IPs, and finding statuses that are almost
always CUI, so all parsing/generation stays local. The model never sees raw XML:
this exposes a compact per-rule view (id, severity, check content, fix text,
status) for the LLM to assess one at a time, and writes results back by editing
the parsed structure IN PLACE — so regenerating produces a well-formed file that
re-imports into STIG Viewer / eMASS without breaking the schema.

Stdlib only (xml.etree + json). All logic original to Drydock.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Canonical statuses + how each format spells them.
STATUSES = ("open", "not_a_finding", "not_applicable", "not_reviewed")
_CKL_OUT = {"open": "Open", "not_a_finding": "NotAFinding",
            "not_applicable": "Not_Applicable", "not_reviewed": "Not_Reviewed"}


def canonical_status(s: str | None) -> str:
    k = (s or "").strip().lower().replace(" ", "_")
    return {
        "open": "open", "notafinding": "not_a_finding", "not_a_finding": "not_a_finding",
        "not_applicable": "not_applicable", "notapplicable": "not_applicable",
        "not_reviewed": "not_reviewed", "notreviewed": "not_reviewed",
    }.get(k, "not_reviewed")


@dataclass
class Rule:
    group_id: str            # Vuln_Num, e.g. V-230222
    rule_id: str             # SV-230222r...
    title: str
    severity: str            # high/medium/low (CAT I/II/III)
    check_content: str
    fix_text: str
    status: str              # canonical
    finding_details: str = ""
    comments: str = ""
    cci: str = ""            # Control Correlation Identifier (maps up to a NIST control)
    _raw: Any = field(default=None, repr=False)  # ET element or dict, for edit-in-place

    def summary(self) -> str:
        return f"{self.group_id} ({self.rule_id}) sev={self.severity} status={self.status} — {self.title}"


class Checklist:
    """A parsed checklist with edit-in-place fidelity. Format is 'ckl' or 'cklb'."""

    def __init__(self, fmt: str) -> None:
        self.fmt = fmt
        self.asset: dict = {}
        self.stig_name: str = ""
        self.stig_version: str = ""
        self.rules: list[Rule] = []
        self._tree: Any = None   # ckl: ElementTree
        self._data: Any = None   # cklb: dict

    def by_id(self, ident: str) -> Rule | None:
        ident = ident.strip().lower()
        for r in self.rules:
            if ident in (r.group_id.lower(), r.rule_id.lower()):
                return r
        # tolerate a base rule id without the trailing revision (SV-230222 vs SV-230222r1)
        for r in self.rules:
            if r.rule_id.lower().startswith(ident) or r.group_id.lower().startswith(ident):
                return r
        return None

    def update(self, ident: str, *, status: str | None = None,
               finding_details: str | None = None, comments: str | None = None) -> bool:
        r = self.by_id(ident)
        if r is None:
            return False
        if status is not None:
            r.status = canonical_status(status)
        if finding_details is not None:
            r.finding_details = finding_details
        if comments is not None:
            r.comments = comments
        self._write_back(r)
        return True

    def counts(self) -> dict:
        c = {s: 0 for s in STATUSES}
        for r in self.rules:
            c[r.status] = c.get(r.status, 0) + 1
        return c

    # ── per-format write-back (edit the raw structure) ──────────────────────
    def _write_back(self, r: Rule) -> None:
        if self.fmt == "cklb":
            d = r._raw
            d["status"] = r.status
            d["finding_details"] = r.finding_details
            d["comments"] = r.comments
        else:  # ckl
            vuln = r._raw
            _set_child_text(vuln, "STATUS", _CKL_OUT[r.status])
            _set_child_text(vuln, "FINDING_DETAILS", r.finding_details)
            _set_child_text(vuln, "COMMENTS", r.comments)

    def to_text(self) -> str:
        if self.fmt == "cklb":
            return json.dumps(self._data, indent=2)
        ET.indent(self._tree, space="  ")
        return ET.tostring(self._tree.getroot(), encoding="unicode")

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_text(), encoding="utf-8")


def _set_child_text(el: ET.Element, tag: str, text: str) -> None:
    child = el.find(tag)
    if child is None:
        child = ET.SubElement(el, tag)
    child.text = text or ""


# ── parsing ─────────────────────────────────────────────────────────────────
def _checklist_from_tree(tree: Any) -> Checklist:
    cl = Checklist("ckl")
    cl._tree = tree
    root = tree.getroot()
    asset = root.find("ASSET")
    if asset is not None:
        cl.asset = {c.tag: (c.text or "") for c in asset}
    info = {si.findtext("SID_NAME"): si.findtext("SID_DATA") for si in root.iter("SI_DATA")}
    cl.stig_name = info.get("title") or info.get("stigid") or ""
    cl.stig_version = info.get("version") or ""
    for vuln in root.iter("VULN"):
        data: dict[str, str] = {}
        for sd in vuln.findall("STIG_DATA"):
            data[sd.findtext("VULN_ATTRIBUTE") or ""] = sd.findtext("ATTRIBUTE_DATA") or ""
        cl.rules.append(Rule(
            group_id=data.get("Vuln_Num", ""), rule_id=data.get("Rule_ID", ""),
            title=data.get("Rule_Title", ""), severity=data.get("Severity", ""),
            check_content=data.get("Check_Content", ""), fix_text=data.get("Fix_Text", ""),
            status=canonical_status(vuln.findtext("STATUS")),
            finding_details=vuln.findtext("FINDING_DETAILS") or "",
            comments=vuln.findtext("COMMENTS") or "", cci=data.get("CCI_REF", ""), _raw=vuln,
        ))
    return cl


def parse_ckl(path: str | Path) -> Checklist:
    return _checklist_from_tree(ET.parse(path))


# ── XCCDF benchmark (the raw STIG) → a blank .ckl ───────────────────────────
_VULN_DISCUSS = re.compile(r"<VulnDiscussion>(.*?)</VulnDiscussion>", re.S | re.I)


def _lname(el) -> str:
    return el.tag.split("}")[-1]


def _kids(parent, name):
    return [c for c in parent if _lname(c) == name]


def _ktext(parent, name) -> str:
    for c in parent:
        if _lname(c) == name:
            return (c.text or "").strip()
    return ""


def parse_xccdf(path: str | Path) -> dict:
    """Parse a DISA STIG XCCDF benchmark (namespace-agnostic) into
    {title, version, release, stigid, rules:[...]}."""
    root = ET.parse(path).getroot()
    title = _ktext(root, "title")
    version = _ktext(root, "version")
    stigid = root.get("id", "")
    release = ""
    for pt in _kids(root, "plain-text"):
        if pt.get("id") == "release-info":
            release = (pt.text or "").strip()
    rules = []
    for group in _kids(root, "Group"):
        gid = group.get("id", "")
        gtitle = _ktext(group, "title")
        for rule in _kids(group, "Rule"):
            desc = _ktext(rule, "description")
            m = _VULN_DISCUSS.search(desc)
            check = ""
            for ch in _kids(rule, "check"):
                check = _ktext(ch, "check-content") or check
            rules.append({
                "group_id": gid, "group_title": gtitle,
                "rule_id": rule.get("id", ""), "rule_ver": _ktext(rule, "version"),
                "severity": rule.get("severity", ""), "weight": rule.get("weight", ""),
                "title": _ktext(rule, "title"),
                "discussion": (m.group(1).strip() if m else desc),
                "check": check, "fix": _ktext(rule, "fixtext"),
                "ccis": [(c.text or "").strip() for c in _kids(rule, "ident")
                         if "cci" in (c.get("system", "") + (c.text or "")).lower()],
            })
    return {"title": title, "version": version, "release": release,
            "stigid": stigid, "rules": rules}


def xccdf_to_checklist(path: str | Path, *, host: str = "") -> Checklist:
    """Build a blank .ckl (all rules Not_Reviewed) from a STIG XCCDF benchmark —
    the inverse of what STIG Viewer does, so the catalog can be assessed directly."""
    bench = parse_xccdf(path)
    stigref = f"{bench['title']} :: Version {bench['version']}, {bench['release']}".strip(" :,")
    root = ET.Element("CHECKLIST")
    asset = ET.SubElement(root, "ASSET")
    for tag, val in [("ROLE", "None"), ("ASSET_TYPE", "Computing"), ("HOST_NAME", host),
                     ("HOST_IP", ""), ("HOST_MAC", ""), ("HOST_FQDN", ""),
                     ("TARGET_COMMENT", ""), ("TECH_AREA", ""), ("TARGET_KEY", ""),
                     ("WEB_OR_DATABASE", "false"), ("WEB_DB_SITE", ""), ("WEB_DB_INSTANCE", "")]:
        ET.SubElement(asset, tag).text = val
    istig = ET.SubElement(ET.SubElement(root, "STIGS"), "iSTIG")
    info = ET.SubElement(istig, "STIG_INFO")
    for name, data in [("version", bench["version"]), ("classification", "UNCLASSIFIED"),
                       ("stigid", bench["stigid"]), ("description", ""),
                       ("releaseinfo", bench["release"]), ("title", bench["title"])]:
        sid = ET.SubElement(info, "SI_DATA")
        ET.SubElement(sid, "SID_NAME").text = name
        ET.SubElement(sid, "SID_DATA").text = data
    for rd in bench["rules"]:
        vuln = ET.SubElement(istig, "VULN")
        for attr, val in [("Vuln_Num", rd["group_id"]), ("Severity", rd["severity"]),
                          ("Group_Title", rd["group_title"]), ("Rule_ID", rd["rule_id"]),
                          ("Rule_Ver", rd["rule_ver"]), ("Rule_Title", rd["title"]),
                          ("Vuln_Discuss", rd["discussion"]), ("Check_Content", rd["check"]),
                          ("Fix_Text", rd["fix"]), ("Weight", rd["weight"]), ("STIGRef", stigref)]:
            sd = ET.SubElement(vuln, "STIG_DATA")
            ET.SubElement(sd, "VULN_ATTRIBUTE").text = attr
            ET.SubElement(sd, "ATTRIBUTE_DATA").text = val or ""
        for cci in rd["ccis"]:
            sd = ET.SubElement(vuln, "STIG_DATA")
            ET.SubElement(sd, "VULN_ATTRIBUTE").text = "CCI_REF"
            ET.SubElement(sd, "ATTRIBUTE_DATA").text = cci
        ET.SubElement(vuln, "STATUS").text = "Not_Reviewed"
        ET.SubElement(vuln, "FINDING_DETAILS").text = ""
        ET.SubElement(vuln, "COMMENTS").text = ""
        ET.SubElement(vuln, "SEVERITY_OVERRIDE").text = ""
        ET.SubElement(vuln, "SEVERITY_JUSTIFICATION").text = ""
    return _checklist_from_tree(ET.ElementTree(root))


def parse_cklb(path: str | Path) -> Checklist:
    cl = Checklist("cklb")
    cl._data = json.loads(Path(path).read_text("utf-8"))
    cl.asset = cl._data.get("target_data", {}) or {}
    for stig in cl._data.get("stigs", []) or []:
        cl.stig_name = cl.stig_name or stig.get("stig_name", stig.get("display_name", ""))
        cl.stig_version = cl.stig_version or str(stig.get("version", ""))
        for rule in stig.get("rules", []) or []:
            ccis = rule.get("ccis") or ([rule["cci"]] if rule.get("cci") else [])
            cl.rules.append(Rule(
                group_id=rule.get("group_id", ""), rule_id=rule.get("rule_id", ""),
                title=rule.get("rule_title", rule.get("group_title", "")),
                severity=rule.get("severity", ""),
                check_content=rule.get("check_content", ""), fix_text=rule.get("fix_text", ""),
                status=canonical_status(rule.get("status")),
                finding_details=rule.get("finding_details", ""),
                comments=rule.get("comments", ""), cci=(ccis[0] if ccis else ""), _raw=rule,
            ))
    return cl


def load(path: str | Path) -> Checklist:
    """Parse a .ckl (XML) or .cklb (JSON) checklist."""
    ext = Path(path).suffix.lower()
    if ext == ".cklb":
        return parse_cklb(path)
    if ext == ".ckl":
        return parse_ckl(path)
    # sniff: JSON → cklb, else ckl
    head = Path(path).read_text("utf-8", "ignore").lstrip()[:1]
    return parse_cklb(path) if head == "{" else parse_ckl(path)


def summary_lines(cl: "Checklist", label: str, status: str | None = None) -> list[str]:
    """Build the `/stig` summary view: counts, an optional status listing (never
    silently truncated), and scale-aware next-step hints. Pure/presentation so it
    can be unit-tested without the TUI."""
    host = cl.asset.get("HOST_NAME") or cl.asset.get("host_name") or "?"
    c = cl.counts()
    lines = [f"STIG checklist {label}  (host: {host}, format: {cl.fmt})",
             f"  {len(cl.rules)} rules — " + " · ".join(f"{k}={v}" for k, v in c.items())]
    if status:
        sf = canonical_status(status)
        hits = [r for r in cl.rules if r.status == sf]
        lines.append(f"\n{sf} ({len(hits)}):")
        lines += [f"  {r.group_id} ({r.severity}) — {r.title}" for r in hits[:50]]
        if len(hits) > 50:  # never silently truncate
            lines.append(f"  … showing first 50 of {len(hits)}; "
                         "open the .ckl in STIG Viewer for the full list.")
        return lines
    nr = c.get("not_reviewed", 0)
    if nr:
        lines.append(f"\nAssess the {nr} un-reviewed rule(s) — one focused turn each:"
                     f"\n  /loop {nr} /stig-assess {label}")
        if nr > 40:
            lines.append(f"  (large checklist — that's {nr} model turns; you can run it in "
                         "batches and re-run /stig to see remaining counts.)")
    if c.get("open"):
        lines.append(f"List open findings to remediate:  /stig {label} open")
    return lines
