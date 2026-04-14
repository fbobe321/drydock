#!/usr/bin/env python3
"""Audit sampler — verifies a random 1-5% of test results are actually strong.

For each sampled test that PASSED, apply stronger checks:
  1. Run the test a second time — output must be deterministic.
  2. If the test uses keyword-grep, check the keyword is NOT trivially present
     in the help text or a no-op invocation of the same command.
  3. For behavioral tests, ensure output is not just echo of input or a
     trivial shell-builtin response.
  4. Flag "fake passes" for manual review.

The output is a JSON summary: total tests, tests sampled, tests flagged as
suspicious, and a per-PRD confidence estimate.

Usage:
    python3 scripts/audit_sampler.py \\
        --root /data3/drydock_test_projects \\
        --sample-rate 0.05      # audit 5% of passing tests
    # Or audit one PRD:
    python3 scripts/audit_sampler.py \\
        --dir /data3/drydock_test_projects/41_calculator
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path


def parse_test_script(script: Path) -> list[dict]:
    """Extract (cmd, check_type, keyword_or_file) tuples from a test script.

    Returns list of {kind, comment, cmd, keyword?, file?}
    """
    text = script.read_text()
    lines = text.split("\n")
    tests = []
    current = None
    for ln in lines:
        if ln.startswith("# Test "):
            if current and current.get("cmd"):
                tests.append(current)
            current = {"kind": "unknown", "comment": ln.strip(),
                       "cmd": None, "keyword": None, "file": None}
            # Peek type from comment
            if "(runs, produces output, no crash)" in ln:
                current["kind"] = "behavioral"
            elif "→ expects" in ln:
                current["kind"] = "keyword"
                m = re.search(r"→ expects '(.+?)'", ln)
                if m:
                    current["keyword"] = m.group(1)
            elif "writes to " in ln:
                current["kind"] = "file_output"
                m = re.search(r"writes to (\S+)", ln)
                if m:
                    current["file"] = m.group(1).rstrip(")")
            continue
        if current is None:
            continue
        if current.get("cmd") is None and ln.strip().startswith("OUT=$("):
            m = re.search(r"OUT=\$\((.+?)\s+2>&1\)", ln)
            if m:
                current["cmd"] = m.group(1).strip()
        elif current.get("cmd") is None and "python3 -m" in ln and "2>" in ln:
            m = re.search(r"(python3 -m \S+.*?)(?:\s+2>|$)", ln)
            if m:
                current["cmd"] = m.group(1).strip()
    if current and current.get("cmd"):
        tests.append(current)
    return tests


def run_cmd(cmd: str, cwd: Path, timeout: int = 30) -> tuple[str, int]:
    try:
        r = subprocess.run(["bash", "-c", cmd], cwd=str(cwd),
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr), r.returncode
    except subprocess.TimeoutExpired:
        return "", 124


def audit_test(test: dict, cwd: Path) -> dict:
    """Run the test's command and check the output passes the claimed check,
    then apply a STRONGER check to see if the pass is shallow."""
    result = {
        "comment": test["comment"],
        "kind": test["kind"],
        "cmd": test.get("cmd"),
        "passes_weak": False,
        "strong_check": None,  # "deterministic", "unique_keyword", etc.
        "strong_ok": None,
        "suspicious": False,
        "note": "",
    }
    if not test.get("cmd"):
        return result

    out1, rc1 = run_cmd(test["cmd"], cwd)

    # Apply claimed check
    kind = test["kind"]
    if kind == "behavioral":
        passes = bool(out1) and "Traceback" not in out1 and \
                 "No module named" not in out1 and \
                 "ModuleNotFoundError" not in out1
    elif kind == "keyword":
        kw = test.get("keyword", "")
        passes = kw.lower() in out1.lower()
    elif kind == "file_output":
        fn = test.get("file", "")
        if fn:
            passes = (cwd / fn).exists() and (cwd / fn).stat().st_size > 0
        else:
            passes = False
    else:
        passes = False
    result["passes_weak"] = passes
    if not passes:
        return result  # not a "pass" in the weak sense

    # Stronger checks now
    # 1) Determinism — run again, compare
    out2, rc2 = run_cmd(test["cmd"], cwd)
    det = out1 == out2
    result["strong_check"] = "determinism"
    result["strong_ok"] = det
    if not det:
        # Some tests are legitimately non-deterministic (password gen,
        # random number guess, timestamp). Note but don't flag as
        # suspicious unless output drastically differs.
        if abs(len(out1) - len(out2)) > max(100, len(out1) * 0.5):
            result["note"] = "output length varies wildly between runs"
            result["suspicious"] = True
        else:
            result["note"] = "output differs between runs (may be legit random)"

    # 2) For keyword tests: check the keyword isn't in --help output
    #    (if it is, the test would pass even on a broken command that
    #     happens to print --help by accident).
    if kind == "keyword" and test.get("keyword"):
        kw = test["keyword"].lower()
        # Take the first 'python3 -m <pkg>' invocation from the cmd
        pm = re.search(r"python3 -m (\S+)", test["cmd"])
        if pm:
            help_cmd = f"python3 -m {pm.group(1)} --help"
            hout, _ = run_cmd(help_cmd, cwd, timeout=15)
            if kw in hout.lower():
                result["note"] = (result["note"] + "; " if result["note"] else "") + \
                    f"keyword '{kw}' ALSO appears in --help — test is near-worthless"
                result["suspicious"] = True

    # 3) For keyword tests: is the keyword too short/common?
    if kind == "keyword" and test.get("keyword"):
        kw = test["keyword"]
        if len(kw) <= 2:
            result["note"] = (result["note"] + "; " if result["note"] else "") + \
                f"keyword '{kw}' is ≤2 chars — likely false positive"
            result["suspicious"] = True
        elif kw.lower() in {"the", "a", "an", "is", "to", "of"}:
            result["note"] = (result["note"] + "; " if result["note"] else "") + \
                f"keyword '{kw}' is a stopword"
            result["suspicious"] = True

    # 4) For behavioral tests: is the output just Python's --help?
    if kind == "behavioral":
        if "usage:" in out1.lower() and "--help" in out1.lower():
            result["note"] = (result["note"] + "; " if result["note"] else "") + \
                "output looks like --help (argparse default) — not a real feature test"
            result["suspicious"] = True
        # Or: is the output a one-line argparse error?
        if "error:" in out1.lower() and len(out1) < 500:
            result["note"] = (result["note"] + "; " if result["note"] else "") + \
                "output is an argparse error — passing fast = fake pass"
            result["suspicious"] = True

    return result


def audit_prd(prd_dir: Path, sample_rate: float) -> dict:
    """Audit a single PRD's tests. Returns summary dict."""
    script = prd_dir / "functional_tests.sh"
    if not script.exists():
        return {"prd": prd_dir.name, "error": "no functional_tests.sh"}
    tests = parse_test_script(script)
    if not tests:
        return {"prd": prd_dir.name, "error": "no tests parsed"}

    n_sample = max(1, int(round(len(tests) * sample_rate)))
    sampled = random.sample(tests, min(n_sample, len(tests)))
    results = [audit_test(t, prd_dir) for t in sampled]

    weak_pass = sum(1 for r in results if r["passes_weak"])
    suspicious = sum(1 for r in results if r["suspicious"])
    confidence = 1.0 - (suspicious / max(1, weak_pass))
    return {
        "prd": prd_dir.name,
        "n_tests": len(tests),
        "n_sampled": len(sampled),
        "n_weak_pass": weak_pass,
        "n_suspicious": suspicious,
        "confidence": round(confidence, 2),
        "details": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="root directory with NN_* PRDs")
    ap.add_argument("--dir", help="audit one specific PRD")
    ap.add_argument("--sample-rate", type=float, default=0.05,
                    help="fraction of tests to audit (default 0.05)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    reports = []
    if args.dir:
        reports.append(audit_prd(Path(args.dir).resolve(), args.sample_rate))
    elif args.root:
        root = Path(args.root).resolve()
        for d in sorted(root.iterdir()):
            if re.match(r"^\d{2,}_", d.name):
                rep = audit_prd(d, args.sample_rate)
                if "error" not in rep:
                    reports.append(rep)
    else:
        print("Need --root or --dir", file=sys.stderr)
        return 1

    total = sum(r["n_sampled"] for r in reports)
    total_susp = sum(r["n_suspicious"] for r in reports)
    total_weak = sum(r["n_weak_pass"] for r in reports)
    print(f"\n=== AUDIT SUMMARY ===")
    print(f"PRDs audited:        {len(reports)}")
    print(f"Tests sampled:       {total}")
    print(f"Weak passes in sample: {total_weak}")
    print(f"Suspicious:          {total_susp}")
    if total_weak:
        conf = 1.0 - total_susp / total_weak
        print(f"Estimated confidence: {conf*100:.1f}%")
    print()

    # Show per-PRD suspicious details
    for r in reports:
        if r["n_suspicious"] > 0:
            print(f"⚠ {r['prd']}: {r['n_suspicious']}/{r['n_sampled']} suspicious")
            for d in r["details"]:
                if d["suspicious"]:
                    print(f"    • {d['comment']}")
                    print(f"      note: {d['note']}")
                    if args.verbose:
                        print(f"      cmd: {d['cmd']}")

    # JSON dump
    (Path("/tmp") / "audit_report.json").write_text(
        json.dumps(reports, indent=2)
    )
    print(f"\nFull report: /tmp/audit_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
