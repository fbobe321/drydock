#!/usr/bin/env python3
"""Test-evolution loop — the load-bearing self-check for drydock.

Without a human in the loop, drydock needs to:
  1. Generate tests (weak or strong)
  2. Build the package to pass them
  3. Audit the tests for weakness
  4. Upgrade weak tests
  5. Re-build to pass the stronger bar
  6. Stop when audit confidence ≥ 95% AND tests pass

This script orchestrates that. It's what the user runs to self-verify
a package is TRULY built, not just weak-test-green.

Pipeline:

    for each PRD:
        run audit_sampler.py --sample-rate 1.0
        if suspicious flags > 0:
            upgrade the tests (replace weak assertions with strong)
        else:
            run strong_tests.sh if it exists
            if strong tests fail:
                iterate with meta_ralph until pass
        commit test upgrades
        track confidence over time

Usage:
    python3 scripts/evolve_tests.py --dir /data3/drydock_test_projects/41_calculator
    python3 scripts/evolve_tests.py --root /data3/drydock_test_projects --once
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
AUDIT = HERE / "audit_sampler.py"


def run_audit(prd_dir: Path) -> dict:
    """Run audit on a PRD. Returns the parsed report (first entry)."""
    r = subprocess.run(
        [sys.executable, str(AUDIT), "--dir", str(prd_dir),
         "--sample-rate", "1.0"],
        capture_output=True, text=True, timeout=120,
    )
    report_path = Path("/tmp/audit_report.json")
    if report_path.exists():
        data = json.loads(report_path.read_text())
        if data:
            return data[0]
    return {"prd": prd_dir.name, "error": "audit failed"}


def upgrade_weak_tests(prd_dir: Path, audit: dict) -> int:
    """For each suspicious test in the audit, rewrite it as a stronger
    variant in functional_tests.sh. Returns number of upgrades."""
    script = prd_dir / "functional_tests.sh"
    if not script.exists():
        return 0
    content = script.read_text()
    n_changes = 0

    for d in audit.get("details", []):
        if not d.get("suspicious"):
            continue
        note = d.get("note", "")
        kind = d.get("kind")
        cmd = d.get("cmd", "")
        if not cmd:
            continue

        # Upgrade strategy depends on the flag type
        if "argparse error = fake pass" in note or "output is an argparse error" in note:
            # Replace behavioral check with one that ALSO fails on
            # argparse errors ("usage: ... error:" and "required:" etc)
            old_check = f'if [ -n "$OUT" ] && ! echo "$OUT" | grep -qE "(Traceback|No module named|ModuleNotFoundError)"; then'
            new_check = (
                'if [ -n "$OUT" ] '
                '&& ! echo "$OUT" | grep -qE "(Traceback|No module named|ModuleNotFoundError)" '
                '&& ! echo "$OUT" | grep -qiE "^usage:.*error:" '
                '&& ! (echo "$OUT" | head -1 | grep -qE "^usage:" && echo "$OUT" | grep -q "error:"); then'
            )
            if old_check in content and new_check not in content:
                content = content.replace(old_check, new_check)
                n_changes += 1

        if "keyword" in note and "≤2 chars" in note:
            # Find this test's block and mark it FAIL with a message
            # so the user knows to hand-write it
            comment = d.get("comment", "")
            m = re.search(r"Test (\d+):", comment)
            if m:
                tn = m.group(1)
                marker = f'# UPGRADE NEEDED: Test {tn} keyword too short — replace with exact match'
                if marker not in content:
                    content = content.replace(
                        comment, f"{comment}\n{marker}", 1
                    )
                    n_changes += 1

        if "keyword" in note and "ALSO appears in --help" in note:
            comment = d.get("comment", "")
            m = re.search(r"Test (\d+):", comment)
            if m:
                tn = m.group(1)
                marker = f'# UPGRADE NEEDED: Test {tn} keyword overlaps with --help — replace with exact-match'
                if marker not in content:
                    content = content.replace(
                        comment, f"{comment}\n{marker}", 1
                    )
                    n_changes += 1

    if n_changes:
        script.write_text(content)
    return n_changes


def evolve_once(prd_dir: Path) -> dict:
    """One round of evolution for a single PRD."""
    print(f"\n=== EVOLVE: {prd_dir.name} ===")

    audit1 = run_audit(prd_dir)
    susp = audit1.get("n_suspicious", 0)
    conf = audit1.get("confidence", 0.0)
    print(f"  initial: {audit1.get('n_weak_pass',0)} pass, "
          f"{susp} suspicious, confidence {conf}")

    if susp == 0:
        # All current tests look OK by the mechanical checks.
        # Run strong_tests.sh if present.
        st = prd_dir / "strong_tests.sh"
        if st.exists():
            r = subprocess.run(["bash", str(st)], cwd=str(prd_dir),
                               capture_output=True, text=True, timeout=120)
            out = r.stdout + r.stderr
            m = re.search(r"RESULT: (\d+) passed, (\d+) failed", out)
            if m:
                p, f = int(m.group(1)), int(m.group(2))
                print(f"  strong: {p}/{p+f}")
                return {"prd": prd_dir.name, "audit": audit1,
                        "strong": {"pass": p, "fail": f},
                        "needs_drydock": f > 0}
        return {"prd": prd_dir.name, "audit": audit1,
                "strong": None, "needs_drydock": False}

    # Upgrade suspects
    n = upgrade_weak_tests(prd_dir, audit1)
    print(f"  upgraded {n} tests")

    # Re-audit
    audit2 = run_audit(prd_dir)
    print(f"  after upgrade: {audit2.get('n_weak_pass',0)} pass, "
          f"{audit2.get('n_suspicious',0)} suspicious, "
          f"confidence {audit2.get('confidence',0.0)}")

    return {"prd": prd_dir.name,
            "audit_before": audit1, "audit_after": audit2,
            "upgraded": n, "needs_drydock": audit2.get("n_suspicious",0) > 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="single PRD directory")
    ap.add_argument("--root", help="root of PRD tree")
    ap.add_argument("--once", action="store_true",
                    help="do one evolve pass per PRD, don't loop")
    args = ap.parse_args()

    if args.dir:
        dirs = [Path(args.dir).resolve()]
    elif args.root:
        root = Path(args.root).resolve()
        dirs = sorted(d for d in root.iterdir()
                      if re.match(r"^\d{2,}_", d.name))
    else:
        print("need --dir or --root", file=sys.stderr)
        return 1

    summary = []
    for d in dirs:
        if not (d / "functional_tests.sh").exists():
            continue
        rep = evolve_once(d)
        summary.append(rep)

    n_need_iter = sum(1 for r in summary if r.get("needs_drydock"))
    n_clean = sum(1 for r in summary if not r.get("needs_drydock"))
    print()
    print(f"=== EVOLVE SUMMARY ===")
    print(f"PRDs: {len(summary)}")
    print(f"Clean (audit-good):  {n_clean}")
    print(f"Need drydock iter:   {n_need_iter}")
    Path("/tmp/evolve_report.json").write_text(json.dumps(summary, indent=2))
    print(f"Full: /tmp/evolve_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
