"""FIAR — Financial Improvement and Audit Readiness (DoD/War Dept audit readiness).

A stdlib-only engine for driving an audit-readiness engagement the way the FIAR
Guidance describes it: assess control objectives within a business-process cycle
against Key Supporting Documents (KSDs), record Notices of Findings and
Recommendations (NFRs), and track Corrective Action Plans (CAPs) through the
five FIAR methodology phases (Discover -> Correct -> Assert -> Validate ->
Sustain).

An "engagement" is one JSON file (analogous to a STIG .ckl): an assessable unit,
a control matrix seeded from the FIAR key-control-objective library, its findings,
and its CAPs. The agent reads and mutates it through the Fiar* tools; a human (or
eMASS/audit workpaper) can consume the JSON directly.

Reference: DoD FIAR Guidance (comptroller.war.gov). All code original to Drydock;
the control library encodes standard, publicly-documented FIAR control objectives.
Not a substitute for an IPA opinion — it structures the readiness work, it does
not render an audit judgement.
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

# ── Controlled vocabularies ───────────────────────────────────────────────
# The five commonly-accepted financial statement assertions the FIAR Guidance
# (April 2017, §1.B / §3) tests against — kept distinct, as the guidance lists
# them (existence and completeness are frequently TESTED together but are
# separate assertions).
ASSERTIONS = {
    "E": "Existence",
    "C": "Completeness",
    "V": "Valuation & Accuracy",
    "PD": "Presentation & Disclosure",
    "RO": "Rights & Obligations",
}
# The FIAR Strategy prioritizes work into Four Waves toward a full-statement
# audit (Guidance Figure 1-1 / §2.C, §5).
WAVES = {
    1: "Appropriations Received / short-term readiness",
    2: "Statement of Budgetary Resources (SBR)",
    3: "Mission-Critical Asset Existence & Completeness",
    4: "Proprietary (full financial statement) audit",
}
# The FIAR Methodology organizes work as numbered KEY TASKS (e.g. 1.3.3 internal
# control testing, 1.4.5 KSD testing) that culminate in a management ASSERTION,
# an independent EXAMINATION/audit (IPA or DoD OIG), then SUSTAINMENT under OMB
# A-123 App. A. These five readiness stages summarize that flow for tracking.
PHASES = ("discover", "correct", "assert", "validate", "sustain")
# Assessable-unit business-process cycles the seed library covers.
CYCLES = {
    "FBWT": "Fund Balance with Treasury",
    "P2P": "Procure-to-Pay",
    "PPE": "Property, Plant & Equipment",
    "INV": "Inventory & Operating Materials",
    "CIVPAY": "Civilian Pay",
    "REIM": "Reimbursables / Order-to-Cash",
    "FR": "Financial Reporting (Budget-to-Report)",
    "ITGC": "IT General Controls",
}
# Control assessment outcomes.
CONTROL_STATUSES = ("not_tested", "effective", "deficient", "not_applicable")
# NFR severity, worst-first (GAO Green Book / A-123 deficiency taxonomy).
SEVERITIES = ("material_weakness", "significant_deficiency", "deficiency")
# CAP lifecycle.
CAP_STATUSES = ("open", "in_progress", "validated", "closed")


_STATUS_ALIASES = {
    "pass": "effective", "passed": "effective", "compliant": "effective",
    "effective": "effective",
    "fail": "deficient", "failed": "deficient", "noncompliant": "deficient",
    "deficient": "deficient",
    "na": "not_applicable", "n_a": "not_applicable", "not_applicable": "not_applicable",
    "open": "not_tested", "untested": "not_tested", "not_tested": "not_tested",
}


def _norm_status(s: str | None) -> str:
    return str(s or "").strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")


def canonical_status(s: str | None) -> str:
    """Lenient: map any input to a status, defaulting to not_tested. Use
    recognized_status() to reject genuinely unknown input at a tool boundary."""
    if not s:
        return "not_tested"
    k = _norm_status(s)
    return _STATUS_ALIASES.get(k, k) if _STATUS_ALIASES.get(k, k) in CONTROL_STATUSES else "not_tested"


def recognized_status(s: str | None) -> bool:
    """True only if s is a known status or alias (not the lenient fallback)."""
    k = _norm_status(s)
    return k in _STATUS_ALIASES or k in CONTROL_STATUSES


def canonical_assertion(a: str) -> str:
    a0 = str(a).strip().upper()
    if a0 in ASSERTIONS:
        return a0
    aliases = {"EXISTENCE": "E", "COMPLETENESS": "C", "ACCURACY": "V",
               "VALUATION": "V", "VALUATION & ACCURACY": "V",
               "PRESENTATION": "PD", "PRESENTATION & DISCLOSURE": "PD",
               "DISCLOSURE": "PD", "RIGHTS": "RO", "RIGHTS & OBLIGATIONS": "RO",
               "OBLIGATIONS": "RO"}
    if a0 in aliases:
        return aliases[a0]
    rev = {v.upper(): k for k, v in ASSERTIONS.items()}
    return rev.get(a0, a0)


# ── Data model ────────────────────────────────────────────────────────────
@dataclass
class Control:
    """One control objective within a cycle, mapped to assertions + KSDs."""
    id: str
    cycle: str                       # CYCLES key
    objective: str                   # the control objective / risk it addresses
    activity: str = ""               # the control activity that should exist
    assertions: list[str] = field(default_factory=list)  # ASSERTIONS keys
    fro: str = ""                    # Financial Reporting Objective it supports
    ksds: list[str] = field(default_factory=list)         # Key Supporting Documents
    test_procedure: str = ""         # how to test it
    key_task: str = ""               # FIAR Methodology key task (e.g. "1.3.3", "1.4.5")
    itgc: bool = False               # is this an IT General Control?
    status: str = "not_tested"
    evidence: str = ""               # what was observed / KSDs examined
    sample_size: int = 0
    exceptions: int = 0              # test exceptions found in the sample
    evidence_chain: dict = field(default_factory=dict)  # EVIDENCE_CHAIN link -> evidence

    def summary(self) -> str:
        mark = {"effective": "✓", "deficient": "✗",
                "not_applicable": "–", "not_tested": "·"}[self.status]
        asrt = "/".join(self.assertions) or "-"
        return f"{mark} {self.id} [{self.cycle}/{asrt}] {self.objective[:70]}"


@dataclass
class Finding:
    """A Notice of Findings and Recommendations (NFR) — the audit finding form."""
    id: str
    control_id: str = ""
    condition: str = ""              # what is (the deficiency observed)
    criteria: str = ""               # what should be (policy/standard/GAAP)
    cause: str = ""                  # why the gap exists (root cause)
    effect: str = ""                 # impact on the assertion / balance
    recommendation: str = ""
    severity: str = "deficiency"
    assertions: list[str] = field(default_factory=list)
    status: str = "open"

    def summary(self) -> str:
        return f"[{self.severity}] {self.id} ({self.control_id or 'entity'}): {self.condition[:70]}"


@dataclass
class Cap:
    """A Corrective Action Plan addressing one NFR, with milestones."""
    id: str
    finding_id: str
    root_cause: str = ""
    action: str = ""
    responsible: str = ""            # responsible organization / POC
    target_date: str = ""            # ISO date
    status: str = "open"
    milestones: list[dict] = field(default_factory=list)  # [{desc, due, done}]

    def summary(self) -> str:
        done = sum(1 for m in self.milestones if m.get("done"))
        return (f"[{self.status}] {self.id} -> {self.finding_id}: {self.action[:60]} "
                f"({done}/{len(self.milestones)} milestones, due {self.target_date or 'TBD'})")


class Engagement:
    """One audit-readiness engagement, persisted as a single JSON file."""

    def __init__(self, data: dict) -> None:
        self._d = data
        self._d.setdefault("controls", [])
        self._d.setdefault("findings", [])
        self._d.setdefault("caps", [])

    # -- persistence --
    @classmethod
    def load(cls, path: str | Path) -> "Engagement":
        return cls(json.loads(Path(path).read_text("utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self._d, indent=2), "utf-8")

    # -- header --
    @property
    def name(self) -> str: return self._d.get("engagement", "")
    @property
    def cycle(self) -> str: return self._d.get("assessable_unit", "")
    @property
    def phase(self) -> str: return self._d.get("phase", "discover")
    @property
    def wave(self) -> int: return int(self._d.get("wave", 4))

    def set_phase(self, phase: str) -> bool:
        if phase not in PHASES:
            return False
        self._d["phase"] = phase
        return True

    # -- controls --
    @property
    def controls(self) -> list[Control]:
        return [Control(**c) for c in self._d["controls"]]

    def control(self, cid: str) -> Control | None:
        for c in self._d["controls"]:
            if c.get("id", "").lower() == cid.lower():
                return Control(**c)
        return None

    def _raw_control(self, cid: str) -> dict | None:
        for c in self._d["controls"]:
            if c.get("id", "").lower() == cid.lower():
                return c
        return None

    def assess(self, cid: str, *, status: str | None = None, evidence: str | None = None,
               sample_size: int | None = None, exceptions: int | None = None,
               chain: dict | None = None) -> bool:
        c = self._raw_control(cid)
        if c is None:
            return False
        if status is not None:
            c["status"] = canonical_status(status)
        if evidence is not None:
            c["evidence"] = evidence
        if sample_size is not None:
            c["sample_size"] = int(sample_size)
        if exceptions is not None:
            c["exceptions"] = int(exceptions)
        if chain is not None:
            merged = dict(c.get("evidence_chain") or {})
            merged.update({k: v for k, v in chain.items() if k in EVIDENCE_CHAIN})
            c["evidence_chain"] = merged
        return True

    def counts(self) -> dict:
        c = {s: 0 for s in CONTROL_STATUSES}
        for ctrl in self._d["controls"]:
            c[canonical_status(ctrl.get("status"))] = c.get(canonical_status(ctrl.get("status")), 0) + 1
        return c

    # -- findings (NFRs) --
    @property
    def findings(self) -> list[Finding]:
        return [Finding(**f) for f in self._d["findings"]]

    def add_finding(self, **kw) -> Finding:
        fid = kw.get("id") or f"NFR-{len(self._d['findings']) + 1:03d}"
        kw["id"] = fid
        kw["assertions"] = [canonical_assertion(a) for a in (kw.get("assertions") or [])]
        if kw.get("severity") not in SEVERITIES:
            kw["severity"] = "deficiency"
        f = Finding(**{k: v for k, v in kw.items() if k in Finding.__dataclass_fields__})
        self._d["findings"].append(asdict(f))
        return f

    # -- CAPs --
    @property
    def caps(self) -> list[Cap]:
        return [Cap(**c) for c in self._d["caps"]]

    def add_cap(self, **kw) -> Cap:
        cid = kw.get("id") or f"CAP-{len(self._d['caps']) + 1:03d}"
        kw["id"] = cid
        c = Cap(**{k: v for k, v in kw.items() if k in Cap.__dataclass_fields__})
        self._d["caps"].append(asdict(c))
        return c

    # -- readiness roll-up --
    def readiness(self) -> dict:
        cts = self.counts()
        tested = cts["effective"] + cts["deficient"]
        total_applicable = tested + cts["not_tested"]
        pct_tested = round(100 * tested / total_applicable, 1) if total_applicable else 0.0
        pct_effective = round(100 * cts["effective"] / tested, 1) if tested else 0.0
        open_mw = sum(1 for f in self._d["findings"]
                      if f.get("severity") == "material_weakness" and f.get("status") != "closed")
        open_caps = sum(1 for c in self._d["caps"] if c.get("status") != "closed")
        return {
            "phase": self.phase, "counts": cts,
            "pct_tested": pct_tested, "pct_effective": pct_effective,
            "open_findings": sum(1 for f in self._d["findings"] if f.get("status") != "closed"),
            "open_material_weaknesses": open_mw, "open_caps": open_caps,
        }


# ── Evidence chain (the FIAR audit-trail every effective test must trace) ─
# A control test cannot support an assertion unless the evidence traces the full
# audit chain from the recorded population all the way to the FS assertion. This
# is the deterministic backbone that separates a real FIAR review from a checklist.
EVIDENCE_CHAIN = (
    "population",            # the recorded universe the sample was drawn from
    "sample",               # the specific item(s) selected
    "source_transaction",   # the transaction the item represents
    "authorization",        # who was authorized to commit/approve it
    "supporting_document",  # the KSD evidencing it
    "system_posting",       # where it posted in the feeder/accounting system
    "gl_effect",            # its effect in the general ledger
    "assertion",            # the FS assertion it ultimately supports
)


def validate_chain(chain: dict | None) -> dict:
    """Given a chain dict {link: evidence}, report which links are present and
    whether the trace is complete. A link counts as present only if it has a
    non-empty value. Returns {complete, present, missing}."""
    chain = chain or {}
    present = [k for k in EVIDENCE_CHAIN if str(chain.get(k, "")).strip()]
    missing = [k for k in EVIDENCE_CHAIN if k not in present]
    return {"complete": not missing, "present": present, "missing": missing}


# ── Reconciliation (a core FIAR substantive procedure, e.g. FBWT tie-out) ─
def reconcile(entity: float, source: float, *, tolerance: float = 0.0) -> dict:
    """Tie an entity balance to an authoritative source (e.g. Fund Balance with
    Treasury vs Treasury's balance). Returns the difference and whether it clears
    the tolerance — the shape of every FIAR reconciliation control."""
    diff = round(float(entity) - float(source), 2)
    return {
        "entity": round(float(entity), 2), "source": round(float(source), 2),
        "difference": diff, "abs_difference": abs(diff),
        "tolerance": float(tolerance), "reconciled": abs(diff) <= float(tolerance),
    }


# ── Seed control library — standard FIAR key control objectives ───────────
# A representative, extensible set of key control objectives per cycle, each
# mapped to the assertion(s) it supports and the KSDs that evidence it.
def _expand_assertions(codes: list[str]) -> list[str]:
    """Normalize assertion codes to the five keys, expanding the E&C pairing."""
    out: list[str] = []
    for x in codes:
        xu = str(x).strip().upper()
        if xu == "EC":
            out += ["E", "C"]
        elif xu == "VA":
            out.append("V")
        else:
            out.append(canonical_assertion(xu))
    seen: set[str] = set()
    return [a for a in out if not (a in seen or seen.add(a))]


# Most seed controls are internal-control tests (key task 1.3.3); KSD-existence
# and reconciliation tests are called out individually.
def _c(cid, cycle, objective, assertions, activity, ksds, test,
       itgc=False, fro="", key_task="1.3.3") -> dict:
    return asdict(Control(id=cid, cycle=cycle, objective=objective, activity=activity,
                          assertions=_expand_assertions(assertions), fro=fro, ksds=ksds,
                          test_procedure=test, key_task=key_task, itgc=itgc))

SEED_CONTROLS: dict[str, list[dict]] = {
    "FBWT": [
        _c("FBWT-01", "FBWT", "Monthly reconciliation of Fund Balance with Treasury to the Treasury balance is performed and reviewed.",
           ["EC", "VA"], "Preparer reconciles GL FBWT to TAS/BETC in GTAS/CARS; independent reviewer signs off.",
           ["Reconciliation workpaper", "GTAS/CARS statement", "GL trial balance"],
           "Select a month; recompute the tie-out; confirm differences are researched and cleared and the review signature is present and timely."),
        _c("FBWT-02", "FBWT", "Undistributed and in-transit disbursements/collections are researched and resolved timely.",
           ["EC", "VA"], "Aging of unmatched transactions; resolution within policy threshold.",
           ["UDO/in-transit aging report", "Resolution documentation"],
           "Sample aged items; verify each was researched and cleared within the policy window."),
    ],
    "P2P": [
        _c("P2P-01", "P2P", "Obligations are recorded only for valid, authorized commitments supported by an obligating document.",
           ["EC", "RO"], "3-way check: obligating document exists, is signed by a warranted official, and matches the recorded obligation.",
           ["Obligating document (contract/PO/MIPR)", "Warrant/appointment", "GL obligation entry"],
           "Sample recorded obligations; trace each to a signed obligating document by a warranted official for the recorded amount and period of availability."),
        _c("P2P-02", "P2P", "Disbursements are made only for goods/services received and correctly matched (three-way match).",
           ["EC", "VA"], "Invoice matched to receiving report and obligating document before payment.",
           ["Invoice", "Receiving report / acceptance", "Obligating document", "Payment voucher"],
           "Sample disbursements; verify a valid three-way match and that the paid amount agrees to the matched documents."),
        _c("P2P-03", "P2P", "Purchases obligate against an available, correct appropriation (period, purpose, amount).",
           ["RO", "PD"], "Appropriation/line of accounting validated at commitment; ADA edits enforced.",
           ["Line of accounting", "Funds availability check"],
           "Sample obligations; confirm the appropriation charged is correct as to purpose, time, and amount (Antideficiency edits present)."),
    ],
    "PPE": [
        _c("PPE-01", "PPE", "General PP&E recorded in the property system exists and is in the entity's possession/control.",
           ["EC", "RO"], "Periodic physical inventory reconciled to the accountable property system of record (APSR).",
           ["APSR listing", "Physical inventory count sheets", "Acquisition document"],
           "Select recorded assets; physically observe existence; select floor assets; trace to the APSR (two-directional existence & completeness)."),
        _c("PPE-02", "PPE", "PP&E is valued at capitalized acquisition cost and depreciated per policy.",
           ["VA"], "Capitalization threshold applied; depreciation method/life per policy; recomputation.",
           ["Acquisition/CLIN cost documentation", "Depreciation schedule"],
           "Sample assets; recompute capitalized cost from KSDs and recompute depreciation; compare to the GL."),
    ],
    "INV": [
        _c("INV-01", "INV", "Inventory and operating materials & supplies recorded in the GL exist and are complete.",
           ["EC"], "Cyclic counts reconciled to the inventory system and GL.",
           ["Count sheets", "Inventory system extract", "GL balance"],
           "Two-directional test: recorded-to-floor and floor-to-recorded; confirm count differences are adjusted."),
    ],
    "CIVPAY": [
        _c("CIVPAY-01", "CIVPAY", "Civilian pay is disbursed only to bona fide employees at authorized rates.",
           ["EC", "VA"], "Payroll reconciled to time & attendance and to authorized personnel actions (SF-50).",
           ["SF-50 personnel action", "Certified T&A", "Payroll register"],
           "Sample pay transactions; trace to a valid SF-50 and certified T&A; recompute gross pay at the authorized rate."),
    ],
    "REIM": [
        _c("REIM-01", "REIM", "Reimbursable orders are supported by a valid agreement and revenue is recognized as earned.",
           ["EC", "RO", "VA"], "Order acceptance documented; earnings recognized on performance.",
           ["Reimbursable agreement (e.g. 7600A/B)", "Performance/delivery evidence"],
           "Sample reimbursable revenue; trace to an accepted order and to evidence of performance for the recognized amount."),
    ],
    "FR": [
        _c("FR-01", "FR", "Journal vouchers (especially manual/top-side) are supported, reviewed, and approved.",
           ["VA", "PD"], "JV supported by documentation; prepared/reviewed by different people (SoD).",
           ["Journal voucher", "Supporting analysis", "Approval evidence"],
           "Sample JVs (bias to manual/top-side); verify support, an independent review, and that preparer≠approver."),
        _c("FR-02", "FR", "The trial balance ties to the financial statements and to GTAS with no unsupported abnormal balances.",
           ["EC", "VA", "PD"], "TB-to-FS crosswalk; GTAS edit/validation checks pass.",
           ["Crosswalk workpaper", "GTAS edit report"],
           "Recompute the TB-to-FS tie-out; confirm GTAS validation/edit checks are clean or explained."),
    ],
    "ITGC": [
        _c("ITGC-01", "ITGC", "Access to financial systems is authorized, least-privilege, and periodically recertified.",
           ["EC", "VA"], "Provisioning approved; quarterly access recertification; timely deprovisioning.",
           ["Access request/approval", "Recertification listing", "Separation records"],
           "Sample users; verify approved provisioning, recertification, and that separated users were disabled timely.", itgc=True),
        _c("ITGC-02", "ITGC", "Segregation of duties prevents one user from both initiating and approving transactions.",
           ["EC", "RO"], "SoD ruleset enforced in the system; conflicts identified and mitigated.",
           ["SoD matrix", "Role assignments", "Conflict/mitigation log"],
           "Obtain the role-to-user mapping; test for incompatible-duty combinations against the SoD matrix.", itgc=True),
        _c("ITGC-03", "ITGC", "Changes to financial systems are authorized, tested, and approved before production.",
           ["VA"], "Change tickets approved; test evidence retained; migration by a separate role.",
           ["Change ticket", "Test evidence", "Approval/migration record"],
           "Sample production changes; verify authorization, testing, approval, and migration SoD.", itgc=True),
    ],
}


# Which FIAR Wave each cycle most naturally falls under.
CYCLE_WAVE = {"FR": 2, "REIM": 2, "P2P": 2, "CIVPAY": 2,   # budgetary / SBR
              "PPE": 3, "INV": 3,                            # asset existence & completeness
              "FBWT": 4, "ITGC": 4}                          # proprietary / cross-cutting


def new_engagement(name: str, cycle: str, wave: int | None = None) -> Engagement:
    """Create an engagement seeded with the key control objectives for a cycle
    (or ALL cycles when cycle is '*' / 'all')."""
    cyc = cycle.strip().upper()
    if cyc in ("*", "ALL"):
        controls = [dict(c) for lst in SEED_CONTROLS.values() for c in lst]
        au = "All cycles"
        w = wave or 4
    else:
        controls = [dict(c) for c in SEED_CONTROLS.get(cyc, [])]
        au = CYCLES.get(cyc, cycle)
        w = wave or CYCLE_WAVE.get(cyc, 4)
    return Engagement({
        "engagement": name, "assessable_unit": au, "cycle": cyc, "wave": w,
        "phase": "discover", "created": date.today().isoformat(),
        "controls": controls, "findings": [], "caps": [],
    })


# ── KSD evidence package (audit binder) ────────────────────────────────────
# Assemble the Key Supporting Documents behind an engagement into a reviewable
# package: a manifest (control → assertion → KSDs → status → evidence chain →
# findings) plus the actual evidence files, zipped. Optionally red-box text
# evidence for a releasable version. Deterministic, stdlib only.
_FILENAME_RE = re.compile(r"[\w][\w./-]*\.[A-Za-z0-9]{1,6}")


def _referenced_files(control: dict, evidence_dir: Path | None) -> list[str]:
    """Filenames named in the control's evidence / chain text that actually exist
    in evidence_dir (so the binder collects the KSDs the test cited)."""
    if not evidence_dir or not evidence_dir.is_dir():
        return []
    avail = {p.name: p for p in evidence_dir.iterdir() if p.is_file()}
    text = " ".join([str(control.get("evidence", "")),
                     *[str(v) for v in (control.get("evidence_chain") or {}).values()]])
    found: list[str] = []
    for m in _FILENAME_RE.findall(text):
        name = Path(m).name
        if name in avail and name not in found:
            found.append(name)
    return found


def build_ksd_package(engagement_path: str | Path, out_dir: str | Path, *,
                      evidence_dir: str | Path | None = None, cycle: str | None = None,
                      status: str | None = None, redact: list[str] | None = None) -> dict:
    """Build a KSD evidence package from an engagement. Collects the cited evidence
    files (from evidence_dir), writes index.json + index.md, optionally red-boxes
    text evidence, and zips the binder. Returns a summary dict."""
    eng = Engagement.load(engagement_path)
    out = Path(out_dir)
    (out / "evidence").mkdir(parents=True, exist_ok=True)
    ev_dir = Path(evidence_dir) if evidence_dir else None
    redact_terms = [t for t in (redact or []) if t.strip()]

    findings_by_control: dict[str, list] = {}
    for f in eng._d.get("findings", []):
        findings_by_control.setdefault(f.get("control_id", ""), []).append(f)

    controls = list(eng._d.get("controls", []))
    if cycle:
        controls = [c for c in controls if str(c.get("cycle", "")).upper() == cycle.upper()]
    if status:
        controls = [c for c in controls if c.get("status") == status]

    entries = []
    collected = 0
    redactions = []
    for c in controls:
        cid = c["id"]
        copied = []
        for name in _referenced_files(c, ev_dir):
            dest = out / "evidence" / cid
            dest.mkdir(parents=True, exist_ok=True)
            dst = dest / name
            shutil.copy(ev_dir / name, dst)  # type: ignore[arg-type]
            if redact_terms and dst.suffix.lower() in (".txt", ".md", ".markdown"):
                from drydock import doccanvas as dc
                fmt = "text" if dst.suffix.lower() == ".txt" else "markdown"
                doc = dc.parse(dst.read_text("utf-8", "replace"), fmt, str(dst))
                n = 0
                for term in redact_terms:
                    try:
                        n += dc.redact(doc, query=term)["redacted"]
                    except dc.PatchError:
                        pass
                if n:
                    dst.write_text(dc.render(doc), "utf-8")
                    redactions.append({"file": f"{cid}/{name}", "redacted": n})
            copied.append(name)
            collected += 1
        entries.append({
            "id": cid, "cycle": c.get("cycle"), "objective": c.get("objective"),
            "assertions": c.get("assertions"), "status": c.get("status", "not_tested"),
            "ksds": c.get("ksds", []), "evidence": c.get("evidence", ""),
            "evidence_chain": validate_chain(c.get("evidence_chain")),
            "evidence_files": copied,
            "findings": [{"id": f.get("id"), "severity": f.get("severity"),
                          "condition": f.get("condition")} for f in findings_by_control.get(cid, [])],
        })

    # For a releasable package, scrub the redaction terms from the MANIFEST too
    # (not just the collected files) so a name/secret can't leak via the binder.
    if redact_terms:
        marker = "[REDACTED]"
        for e in entries:
            for term in redact_terms:
                e["evidence"] = e["evidence"].replace(term, marker)
                for f in e["findings"]:
                    f["condition"] = (f.get("condition") or "").replace(term, marker)

    manifest = {
        "engagement": eng.name, "assessable_unit": eng.cycle, "wave": eng.wave,
        "generated": date.today().isoformat(), "source": Path(engagement_path).name,
        "control_count": len(entries), "evidence_files_collected": collected,
        "redactions": redactions, "controls": entries,
    }
    (out / "index.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "index.md").write_text(_ksd_index_md(manifest), encoding="utf-8")

    zip_path = str(out) + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(out.parent))
    return {"package": str(out), "zip": zip_path, "controls": len(entries),
            "files_collected": collected, "redactions": len(redactions),
            "missing_evidence": [e["id"] for e in entries if not e["evidence_files"]]}


def _ksd_index_md(m: dict) -> str:
    lines = [f"# KSD Evidence Package — {m['engagement']}",
             f"Assessable unit: {m['assessable_unit']} · Wave {m['wave']} · "
             f"generated {m['generated']} from {m['source']}",
             f"Controls: {m['control_count']} · evidence files collected: "
             f"{m['evidence_files_collected']} · redactions: {len(m['redactions'])}", ""]
    for e in m["controls"]:
        lines.append(f"## {e['id']} — {e['objective']}")
        lines.append(f"- Cycle: {e['cycle']}  ·  Assertions: {', '.join(e['assertions'] or [])}"
                     f"  ·  Status: **{e['status']}**")
        lines.append(f"- KSDs required: {', '.join(e['ksds']) or '(none listed)'}")
        ch = e["evidence_chain"]
        lines.append(f"- Evidence chain: {'COMPLETE' if ch['complete'] else 'INCOMPLETE — missing ' + ', '.join(ch['missing'])}")
        if e["evidence"]:
            lines.append(f"- Evidence examined: {e['evidence']}")
        lines.append(f"- Evidence files: {', '.join('evidence/'+e['id']+'/'+f for f in e['evidence_files']) or '⚠ NONE collected'}")
        for f in e["findings"]:
            lines.append(f"- FINDING {f['id']} ({f['severity']}): {f['condition']}")
        lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────
def _main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="drydock.fiar", description="FIAR audit-readiness engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("new"); q.add_argument("path"); q.add_argument("name"); q.add_argument("cycle")
    q.add_argument("--wave", type=int, choices=[1, 2, 3, 4])
    q = sub.add_parser("controls"); q.add_argument("path"); q.add_argument("--status")
    q = sub.add_parser("control"); q.add_argument("path"); q.add_argument("id")
    q = sub.add_parser("assess"); q.add_argument("path"); q.add_argument("id")
    q.add_argument("--status"); q.add_argument("--evidence", default="")
    q.add_argument("--sample", type=int); q.add_argument("--exceptions", type=int)
    q = sub.add_parser("summary"); q.add_argument("path")
    q = sub.add_parser("reconcile"); q.add_argument("entity", type=float)
    q.add_argument("source", type=float); q.add_argument("--tolerance", type=float, default=0.0)
    q = sub.add_parser("cycles")
    q = sub.add_parser("package"); q.add_argument("path"); q.add_argument("out")
    q.add_argument("--evidence-dir"); q.add_argument("--cycle"); q.add_argument("--status")
    q.add_argument("--redact", action="append", default=[])
    a = p.parse_args(argv)

    if a.cmd == "cycles":
        for k, v in CYCLES.items():
            print(f"{k:8} {v}  ({len(SEED_CONTROLS.get(k, []))} controls)")
        return 0
    if a.cmd == "reconcile":
        r = reconcile(a.entity, a.source, tolerance=a.tolerance)
        print(json.dumps(r, indent=2)); return 0 if r["reconciled"] else 1
    if a.cmd == "package":
        r = build_ksd_package(a.path, a.out, evidence_dir=a.evidence_dir,
                              cycle=a.cycle, status=a.status, redact=a.redact)
        print(f"KSD package: {r['controls']} controls, {r['files_collected']} evidence file(s)"
              + (f", {r['redactions']} file(s) redacted" if r["redactions"] else "")
              + f" → {r['package']}/  (+ {Path(r['zip']).name})")
        if r["missing_evidence"]:
            print(f"⚠ no evidence file collected for: {', '.join(r['missing_evidence'])}")
        return 0
    if a.cmd == "new":
        eng = new_engagement(a.name, a.cycle, wave=a.wave); eng.save(a.path)
        print(f"created {a.path}: {eng.name} / {eng.cycle} (Wave {eng.wave}) "
              f"— {len(eng.controls)} controls")
        if not eng.controls and a.cycle.strip().upper() not in ("*", "ALL"):
            print(f"⚠ 0 controls seeded — '{a.cycle}' is not a known cycle. Valid cycles: "
                  f"{', '.join(CYCLES)}, or ALL. (See: python -m drydock.fiar cycles)")
        return 0

    eng = Engagement.load(a.path)
    if a.cmd == "controls":
        sf = canonical_status(a.status) if a.status else None
        rows = [c for c in eng.controls if sf is None or c.status == sf]
        print(f"{eng.name} [{eng.cycle}] Wave {eng.wave} phase={eng.phase} — " +
              ", ".join(f"{k}={v}" for k, v in eng.counts().items()))
        for c in rows:
            print("  " + c.summary())
    elif a.cmd == "control":
        c = eng.control(a.id)
        if not c:
            print(f"no control {a.id}"); return 1
        print(f"{c.id} [{eng.cycle}] — {c.objective}\nAssertions: {', '.join(c.assertions)}\n"
              f"Activity: {c.activity}\nKSDs: {', '.join(c.ksds)}\nTest: {c.test_procedure}\n"
              f"Status: {c.status}  sample={c.sample_size} exceptions={c.exceptions}\n"
              f"Evidence: {c.evidence or '(none)'}")
    elif a.cmd == "assess":
        ok = eng.assess(a.id, status=a.status, evidence=a.evidence or None,
                        sample_size=a.sample, exceptions=a.exceptions)
        if not ok:
            print(f"no control {a.id}"); return 1
        eng.save(a.path); print(f"✓ {a.id} -> {canonical_status(a.status)}")
    elif a.cmd == "summary":
        print(json.dumps(eng.readiness(), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
