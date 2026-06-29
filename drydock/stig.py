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
def parse_ckl(path: str | Path) -> Checklist:
    cl = Checklist("ckl")
    cl._tree = ET.parse(path)
    root = cl._tree.getroot()
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
