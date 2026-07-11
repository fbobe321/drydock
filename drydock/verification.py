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
