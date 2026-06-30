"""eMASS-compatible POA&M (Plan of Action & Milestones) export from STIG findings.

Deterministic and stdlib-only: takes the OPEN findings in a parsed checklist,
maps each to its NIST SP 800-53 control (via the DISA CCI map in cci.py) and its
eMASS severity (STIG CAT I/II/III), and writes the standard eMASS POA&M CSV. No
LLM in this path — it's a faithful transform of facts already in the checklist +
the CCI map, so it's reproducible and ungameable. Complements the `/rmf-poam`
skill (which writes a narrative entry); this is the bulk machine-importable CSV.

All logic original to Drydock.
"""
from __future__ import annotations

import csv
from pathlib import Path

# STIG severity (CAT I/II/III) → eMASS Severity + the raw CAT label.
_EMASS_SEVERITY = {"high": "High", "medium": "Moderate", "low": "Low"}
_CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}

# eMASS POA&M columns (the prompt's required fields + the standard companions
# eMASS expects so the CSV imports cleanly).
EMASS_HEADERS = [
    "Control",
    "Vulnerability Description",
    "Source Identifying Vulnerability",
    "POA&M Status",
    "Milestone Description",
    "Severity",
    "Raw Severity",
    "Comments",
]


def poam_rows(checklist, cci_map: dict | None = None) -> list[dict]:
    """One eMASS POA&M row per OPEN finding. `cci_map` (CCI→control) populates the
    Control column; missing mappings are marked '(unmapped CCI)' rather than
    dropped, so nothing is silently lost."""
    cci_map = cci_map or {}
    rows: list[dict] = []
    for r in checklist.rules:
        if r.status != "open":
            continue
        ctl = cci_map.get(r.cci, "")
        control = ctl.upper() if ctl else (f"(unmapped {r.cci})" if r.cci else "(no CCI)")
        rows.append({
            "Control": control,
            "Vulnerability Description": f"{r.group_id} ({r.rule_id}): {r.title}".strip(),
            "Source Identifying Vulnerability": r.rule_id or r.group_id,
            "POA&M Status": "Ongoing",
            "Milestone Description": (r.fix_text or "").strip() or "See STIG Fix Text.",
            "Severity": _EMASS_SEVERITY.get(r.severity, "Moderate"),
            "Raw Severity": _CAT.get(r.severity, "CAT II"),
            "Comments": (r.finding_details or "").strip(),
        })
    return rows


def write_csv(rows: list[dict], path: str | Path) -> int:
    """Write POA&M rows to an eMASS-headered CSV. Returns the row count."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EMASS_HEADERS)
        w.writeheader()
        for row in rows:
            w.writerow({h: row.get(h, "") for h in EMASS_HEADERS})
    return len(rows)


def export(checklist, cci_map: dict | None, out_path: str | Path) -> dict:
    """Build + write the POA&M CSV for a checklist's open findings."""
    rows = poam_rows(checklist, cci_map)
    write_csv(rows, out_path)
    return {"rows": len(rows), "path": str(out_path)}
