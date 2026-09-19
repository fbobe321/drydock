"""Verifier integrity — did the fitness function itself get edited?

Mitigation 2 for MCR PRD Appendix A.4. A holdout (mitigation 1) validates the CODE; it
cannot validate the SUITE. Observed directly: given an unsatisfiable requirement, the
agent deleted the offending test, wrote a genuinely correct implementation, and the
primary suite then reported a full pass. The holdout confirmed it — correctly, since the
code really did satisfy every remaining requirement — and the run was recorded as SOLVED
and `verified` for a spec that was never met.

The attack is not subtle and the check need not be either: if the files that define the
score changed since the ratchet's base ref, a "full pass" is not evidence about the
original task.

Advisory by design (project rule): this reports, it does not mutate the working tree.
Restoring files mid-run risks destroying real work; naming the edited files lets the
operator judge. Detection is what was missing, not enforcement machinery.
"""
from __future__ import annotations

import subprocess

# Paths that define the score. Deliberately broad: a false positive costs a warning,
# a false negative lets an edited scorer through.
TEST_MARKERS = (
    "tests/", "test/", "spec/", "conftest.py",
    "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "Makefile",
)
TEST_NAME_MARKERS = ("test_", "_test.", ".spec.", ".test.")


def looks_like_verifier_file(path: str) -> bool:
    p = (path or "").replace("\\", "/")
    if any(m in p for m in TEST_MARKERS):
        return True
    name = p.rsplit("/", 1)[-1]
    return any(m in name for m in TEST_NAME_MARKERS)


def changed_files(repo: str, base_ref: str) -> list:
    """Files changed in the working tree relative to `base_ref`. Empty on any git
    failure — integrity checking must never break a run, and an unavailable check is
    not evidence of tampering."""
    if not base_ref:
        return []
    try:
        r = subprocess.run(["git", "diff", "--name-only", base_ref, "--"],
                           cwd=repo, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return []
        return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def edited_verifier_files(repo: str, base_ref: str) -> list:
    """The subset of changed files that define the score."""
    return [f for f in changed_files(repo, base_ref) if looks_like_verifier_file(f)]


def integrity_note(edited: list) -> str:
    """Operator-facing warning naming what changed, or "" when clean."""
    if not edited:
        return ""
    shown = ", ".join(edited[:6]) + (f" (+{len(edited) - 6} more)" if len(edited) > 6 else "")
    return ("⚠ VERIFIER INTEGRITY: the files that define the score were modified during "
            f"this run — {shown}. A full pass is not evidence about the ORIGINAL task; "
            "the suite it passed is not the suite it was given. Inspect the diff before "
            "accepting this result.")
