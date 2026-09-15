#!/usr/bin/env python3
"""Offline STIG -> assessed checklist -> eMASS POA&M, end to end, with no network
and no LLM. This is the deterministic compliance pipeline that Drydock's agent
drives interactively (the /stig-* skills); running it directly here makes the
artifact reproducible and reviewable.

    python examples/stig-poam/run.py

Reads  benchmark-xccdf.xml (a sample DISA STIG excerpt),
writes  out/assessed.ckl   (a completed DISA .ckl with statuses + finding details)
        out/poam.csv        (eMASS POA&M for the OPEN findings, mapped to NIST 800-53)

Everything stays on this machine — the point of the tool: assess CUI-bearing
systems where a cloud agent is not allowed.
"""
from __future__ import annotations

from pathlib import Path

from drydock import poam, stig

HERE = Path(__file__).parent
OUT = HERE / "out"

# DISA publishes the CCI -> NIST 800-53 mapping; Drydock caches it offline (cci.py).
# For a self-contained example we inline just the four CCIs this benchmark uses.
CCI_TO_800_53 = {
    "CCI-000054": "AC-10",   # concurrent session control
    "CCI-000197": "IA-5",    # authenticator (password) transmission
    "CCI-000048": "AC-8",    # system use notification (DoD banner)
    "CCI-002450": "SC-13",   # cryptographic protection (FIPS)
}

# The findings an assessor (or the agent, after examining evidence) recorded.
# status: open | notafinding | not_applicable | not_reviewed
ASSESSMENTS = {
    "V-222387": ("open",        "No concurrent-session limit is configured; unlimited sessions per account."),
    "V-222388": ("open",        "Login form posts credentials over plain HTTP; no TLS on the auth endpoint."),
    "V-222389": ("notafinding", "Standard Mandatory DoD Notice and Consent Banner is displayed at logon."),
    "V-222390": ("open",        "TLS offers a non-FIPS cipher suite (CHACHA20); no FIPS mode enforced."),
}


def main() -> None:
    OUT.mkdir(exist_ok=True)

    # 1) STIG benchmark (XCCDF) -> a blank, assessable DISA .ckl
    cl = stig.xccdf_to_checklist(HERE / "benchmark-xccdf.xml", host="appsrv1")
    print(f"parsed {len(cl.rules)} rules from the STIG benchmark → blank checklist")

    # 2) record the assessment results (edit-in-place, DISA-faithful .ckl)
    for ident, (status, details) in ASSESSMENTS.items():
        ok = cl.update(ident, status=status, finding_details=details)
        assert ok, f"rule {ident} not found in benchmark"
    counts = cl.counts()
    print(f"assessed: open={counts['open']} not_a_finding={counts['not_a_finding']} "
          f"not_reviewed={counts['not_reviewed']}")

    ckl_path = OUT / "assessed.ckl"
    cl.save(ckl_path)
    print(f"wrote completed checklist → {ckl_path.relative_to(HERE)}")

    # 3) OPEN findings -> eMASS POA&M CSV, each mapped to its NIST 800-53 control
    csv_path = OUT / "poam.csv"
    r = poam.export(cl, CCI_TO_800_53, csv_path)
    print(f"wrote eMASS POA&M ({r['rows']} open findings) → {csv_path.relative_to(HERE)}")

    print("\nPOA&M (Control ← CCI):")
    for row in poam.poam_rows(cl, CCI_TO_800_53):
        print(f"  {row['Control']:6s} {row['Raw Severity']:7s} {row['Source Identifying Vulnerability']}"
              f"  — {row['Vulnerability Description'].split(':',1)[-1].strip()}")


if __name__ == "__main__":
    main()
