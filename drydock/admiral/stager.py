"""Phase 3b: stage a validated proposal as a local branch + proposal markdown.

NEVER pushes, NEVER merges to main, NEVER publishes. Writes a summary
file in `~/.drydock/admiral_proposals/<ts>.md` describing the diff,
rationale, validator output, branch name, and explicit rollback
command. The human then runs `/admiral-apply <branch>` to merge.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from drydock.admiral import history, persistence
from drydock.admiral.proposer import Proposal
from drydock.admiral.validator import REPO_ROOT, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class StagedProposal:
    branch: str
    proposal_path: Path
    commit_sha: str


def _run(cmd: list[str], cwd: Path = REPO_ROOT, timeout: int = 60) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd, cwd=cwd, timeout=timeout,
        capture_output=True, text=True, check=False,
    )
    return p.returncode, p.stdout, p.stderr


def _safe_code(code: str) -> str:
    # Branch name can't have weird chars — keep letters, digits, hyphens.
    out = []
    for ch in code:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append("-")
    return "".join(out)[:40]


def stage(proposal: Proposal, validation: ValidationResult) -> StagedProposal | None:
    if not validation.ok:
        raise ValueError("refusing to stage a proposal that didn't pass validation")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"admiral/{ts}-{_safe_code(proposal.code)}"

    # Invariant: we ALWAYS branch from main.
    rc, out, err = _run(["git", "rev-parse", "main"])
    if rc != 0:
        logger.error("Admiral stager: cannot resolve 'main' ref: %s", err)
        return None
    main_sha = out.strip()

    # Create branch pointing at main (no checkout of main working tree).
    rc, out, err = _run(["git", "branch", branch, main_sha])
    if rc != 0:
        logger.error("Admiral stager: branch create failed: %s", err)
        return None

    # Use a short-lived worktree to apply+commit, then drop the worktree.
    import tempfile
    tmp_wt = Path(tempfile.mkdtemp(prefix="admiral_stage_")) / "wt"
    diff_path = tmp_wt.parent / "proposal.diff"
    diff_path.write_text(proposal.diff if proposal.diff.endswith("\n")
                         else proposal.diff + "\n")

    commit_sha: str | None = None
    try:
        rc, out, err = _run(
            ["git", "worktree", "add", str(tmp_wt), branch], cwd=REPO_ROOT,
        )
        if rc != 0:
            logger.error("Admiral stager: worktree add failed: %s", err)
            _run(["git", "branch", "-D", branch])
            return None
        rc, out, err = _run(["git", "apply", str(diff_path)], cwd=tmp_wt)
        if rc != 0:
            logger.error("Admiral stager: diff apply failed: %s", err)
            return None
        rc, out, err = _run(["git", "add", "-A"], cwd=tmp_wt)
        if rc != 0:
            return None
        commit_msg = (
            f"admiral-proposed: {proposal.code}\n\n"
            f"Rationale: {proposal.rationale}\n\n"
            f"Directives violated: {', '.join(proposal.directives_violated) or '—'}\n"
            f"Source: {proposal.source}\n"
            f"Fingerprint: {proposal.fingerprint}\n"
        )
        rc, out, err = _run(
            ["git", "commit", "-m", commit_msg], cwd=tmp_wt,
        )
        if rc != 0:
            logger.error("Admiral stager: commit failed: %s", err)
            return None
        rc, out, err = _run(["git", "rev-parse", "HEAD"], cwd=tmp_wt)
        if rc == 0:
            commit_sha = out.strip()
    finally:
        try:
            _run(["git", "worktree", "remove", "--force", str(tmp_wt)], cwd=REPO_ROOT)
        except Exception:
            pass
        try:
            import shutil
            shutil.rmtree(tmp_wt.parent, ignore_errors=True)
        except Exception:
            pass

    if not commit_sha:
        logger.error("Admiral stager: no commit sha")
        _run(["git", "branch", "-D", branch])
        return None

    # Write the proposal summary markdown.
    persistence.PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    prop_path = persistence.PROPOSALS_DIR / f"{ts}-{_safe_code(proposal.code)}.md"
    prop_path.write_text(
        f"# Admiral proposal: {proposal.code}\n\n"
        f"**Branch:** `{branch}`  \n"
        f"**Commit:** `{commit_sha}`  \n"
        f"**Source:** {proposal.source}  \n"
        f"**Fingerprint:** `{proposal.fingerprint}`  \n"
        f"**Directives violated:** {', '.join(proposal.directives_violated) or '—'}\n\n"
        f"## Rationale\n\n{proposal.rationale}\n\n"
        f"## Diff\n\n```diff\n{proposal.diff}\n```\n\n"
        f"## Validator output (tail)\n\n"
        f"```\n{validation.stdout[-2000:]}\n```\n\n"
        f"## Next actions\n\n"
        f"- Merge: `/admiral-apply {branch}` (or `git merge --no-ff {branch}`)\n"
        f"- Reject: `/admiral-reject {branch}` (deletes branch + records fingerprint)\n"
        f"- Rollback: `git branch -D {branch}`\n"
    )
    history.append(
        "proposal-staged",
        f"{proposal.code} :: branch={branch} :: fp={proposal.fingerprint}",
    )
    return StagedProposal(branch=branch, proposal_path=prop_path, commit_sha=commit_sha)
