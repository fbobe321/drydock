"""Phase 3b: validate a proposed diff against the full test suite in a
git worktree. If green, the stager can promote it to a local branch.

Hard invariants (validated here, enforced by construction):
* Worktree is always a fresh checkout of main.
* Tests run via pytest on the exact command publish_to_pypi.sh uses.
* Worktree is cleaned up even on exception.
* Never touches the main working tree or its git state.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path("/data3/drydock")
# Keep this identical to what publish_to_pypi.sh runs.
RELEASE_GATE = (
    "tests/test_drydock_regression.py tests/test_drydock_tasks.py "
    "tests/test_loop_detection.py tests/test_agent_tasks.py "
    "tests/test_integration.py tests/test_user_issues.py "
    "tests/test_real_issues.py tests/test_admiral.py"
)
PYTHON = "/home/bobef/miniconda3/bin/python3"


@dataclass
class ValidationResult:
    ok: bool
    stdout: str
    stderr: str
    worktree_path: str | None = None


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd, cwd=cwd, timeout=timeout,
        capture_output=True, text=True, check=False,
    )
    return p.returncode, p.stdout, p.stderr


def validate(diff: str, repo_root: Path = REPO_ROOT) -> ValidationResult:
    """Apply diff in a fresh git worktree of main, run release-gate tests."""
    # Create a tempdir for the worktree.
    tmpdir = Path(tempfile.mkdtemp(prefix="admiral_wt_"))
    wt = tmpdir / "wt"
    diff_path = tmpdir / "proposal.diff"
    try:
        diff_path.write_text(diff if diff.endswith("\n") else diff + "\n")
        # 1) Add worktree at main.
        rc, out, err = _run(
            ["git", "worktree", "add", "--detach", str(wt), "main"],
            cwd=repo_root,
        )
        if rc != 0:
            return ValidationResult(False, out, f"worktree add failed: {err}")
        # 2) Apply diff.
        rc, out, err = _run(
            ["git", "apply", "--check", str(diff_path)],
            cwd=wt,
        )
        if rc != 0:
            return ValidationResult(False, out, f"diff does not apply cleanly: {err}",
                                    worktree_path=str(wt))
        rc, out, err = _run(["git", "apply", str(diff_path)], cwd=wt)
        if rc != 0:
            return ValidationResult(False, out, f"diff apply failed: {err}",
                                    worktree_path=str(wt))
        # 3) Run the release-gate tests.
        cmd = [
            PYTHON, "-m", "pytest", *RELEASE_GATE.split(),
            "-p", "no:xdist", "-p", "no:cov",
            "--override-ini=addopts=", "-q",
        ]
        rc, out, err = _run(cmd, cwd=wt, timeout=600)
        if rc != 0:
            return ValidationResult(False, out, f"tests failed:\n{err}",
                                    worktree_path=str(wt))
        return ValidationResult(True, out, "", worktree_path=str(wt))
    finally:
        # Always try to remove the worktree, even on exception.
        try:
            _run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo_root)
        except Exception:
            pass
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
