"""Verification gating (Agent-Buildout PRD, Epic B / verification):

  "A text-only model response is not evidence that a task is complete."

The agent loop uses this to decide, when the model claims "done", whether it
actually VERIFIED — i.e. ran a test, a linter/type-check, a build, or executed the
code it produced. If it changed files but ran nothing, the loop nudges it to verify
instead of accepting completion. Deliberately small (a command classifier + the loop
gate) so it drops into the current loop without a rewrite. Logic original to Drydock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Explicit test / lint / type-check / build commands.
_VERIFY_KEYWORDS = re.compile(
    r"\b(pytest|py\.test|unittest|nosetests?|tox|test\.sh|run_tests|"
    r"make(\s+\w+)?|npm\s+(test|run)|yarn\s+(test|run)|pnpm\s+(test|run)|jest|mocha|vitest|"
    r"cargo\s+(test|check|build|run|clippy)|go\s+(test|build|run|vet)|"
    r"ruff|flake8|pylint|mypy|pyright|eslint|tsc|prettier|"
    r"ctest|cmake|gradle|mvn|rspec|phpunit|dotnet\s+test|bats)\b",
    re.I,
)
# Executing something — running the produced script/binary counts as checking it.
_EXEC = re.compile(r"(?:^|[\s;&|(])(?:python3?|node|deno|bun|bash|sh|ruby|perl|"
                   r"java|javac|gcc|g\+\+|clang|\./|\.\\)")


def looks_like_verification(cmd) -> bool:
    """True if a shell command constitutes verifying work — running tests/checks/
    builds, or executing the produced code. Best-effort; never raises."""
    if not cmd or not isinstance(cmd, str):
        return False
    return bool(_VERIFY_KEYWORDS.search(cmd) or _EXEC.search(cmd))


# ── Criteria-tied evidence (bench finding: exit-0 self-checks ≠ acceptance
# criteria). Three separate traces showed the model "verifying" with checks that
# passed but never touched the files the objective is actually judged on (e.g. a
# vim macro run 23× cleanly while expected.csv was never diffed). These helpers
# let the gate name exactly which stated artifacts the checks never covered.

# File-ish tokens: a path or name with a real extension (/app/expected.csv,
# apply_macros.vim, test.sh). Excludes bare version numbers and trailing dots.
_ARTIFACT = re.compile(r"(?:/[\w.\-/]+/)?([\w\-]+\.[A-Za-z]{1,6})\b")
# Extensions that are prose, not artifacts ("e.g.", "i.e.", "v1.0", "No.1").
_NON_ARTIFACT_EXT = frozenset({"g", "e", "i", "etc", "vs", "no"})


def extract_artifacts(text) -> list[str]:
    """File basenames named in an objective / acceptance criteria — the concrete
    things a verification should touch. Order-preserving, deduped, best-effort;
    [] when the text names no files (the gate then behaves as before)."""
    if not text or not isinstance(text, str):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _ARTIFACT.finditer(text):
        base = m.group(1)
        ext = base.rsplit(".", 1)[-1].lower()
        if ext in _NON_ARTIFACT_EXT or ext.isdigit():
            continue
        if base.lower() not in seen:
            seen.add(base.lower())
            out.append(base)
    return out


def uncovered_artifacts(artifacts, verification_text) -> list[str]:
    """The stated artifacts that `verification_text` (the concatenated commands +
    results of every verification run so far) never references. What comes back
    is exactly what the completion nudge should name."""
    if not artifacts:
        return []
    low = (verification_text or "").lower()
    return [a for a in artifacts if a.lower() not in low]


@dataclass
class VerificationEvidence:
    """Structured outcome of a verification command — did the check PASS, not just
    run (PRD verification/evidence). status: 'pass' | 'fail' | 'unknown'."""
    command: str
    status: str
    tests_passed: int | None = None
    tests_failed: int | None = None
    exit_code: int | None = None
    summary: str = ""


# tool_bash appends "[exit code: N]" ONLY on non-zero exit; absent => exit 0.
_EXIT = re.compile(r"\[exit code:\s*(-?\d+)\]")
# pytest/unittest-style tallies: "3 passed", "2 failed", "1 error".
_TALLY = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b", re.I)


def parse_evidence(command, result_text) -> VerificationEvidence:
    """Classify a verification command's result as pass/fail. Exit code is the
    primary signal (drydock reports non-zero); pytest-style tallies refine it.
    Best-effort; never raises."""
    cmd = command if isinstance(command, str) else ""
    text = result_text if isinstance(result_text, str) else str(result_text or "")
    m = _EXIT.search(text)
    exit_code = int(m.group(1)) if m else 0
    passed = failed = None
    tally = {}
    for n, k in _TALLY.findall(text):
        tally[k.lower().rstrip("s")] = tally.get(k.lower().rstrip("s"), 0) + int(n)
    if tally:
        passed = tally.get("passed")
        failed = tally.get("failed", 0) + tally.get("error", 0)
    status = "fail" if ((failed and failed > 0) or exit_code != 0) else "pass"
    parts = [f"exit {exit_code}"]
    if passed is not None or failed:
        parts.append(f"{passed or 0} passed, {failed or 0} failed")
    return VerificationEvidence(cmd, status, passed, failed, exit_code, "; ".join(parts)[:200])
