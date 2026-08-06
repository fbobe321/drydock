"""Ratchet — cumulative-selection solving: persist verified progress across turns.

The agent's normal retry loop gives a fresh attempt each round, so a task that
needs more than one attempt's worth of work slips all the way back every time
(no pawl). The ratchet applies *cumulative selection* instead:

  * fitness = a **verifier's** passing-check count (a graded signal, not binary),
  * PAWL   = snapshot the workspace whenever the pass-count IMPROVES (lock in),
  * no backslip = if a round doesn't improve, roll the workspace back to the best
    snapshot before the next round.

Same model, no stronger teacher — the verifier is the only external signal, like
an RL reward. This module is pure/stdlib (no TUI imports) so it is unit-testable;
the TUI drives it between agent turns.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass


# ───────────────────────── fitness (verifier → score) ─────────────────────────

# Generic token scan handles pytest ("5 passed, 1 failed"), cargo
# ("test result: ok. 5 passed; 0 failed"), jest ("4 passed, 5 total"), etc.
_PASSED = re.compile(r"(\d+)\s+passed", re.I)
_FAILED = re.compile(r"(\d+)\s+failed", re.I)
_ERRORS = re.compile(r"(\d+)\s+errors?\b", re.I)
_TOTAL = re.compile(r"(\d+)\s+total", re.I)


def score_output(output: str, mode: str, returncode: int) -> tuple[int, int]:
    """Map a verifier's output+exit code to (passed, total).

    mode:
      "exitcode" — pass/fail from the return code → (1,1) or (0,1).
      "auto"     — parse common test-runner summaries; fall back to exitcode.
      other      — a custom regex with 1 group (passed) or 2 groups (passed,total).
    """
    if mode == "exitcode":
        return (1, 1) if returncode == 0 else (0, 1)

    if mode == "auto":
        mp = _PASSED.search(output)
        if mp:
            passed = int(mp.group(1))
            mt = _TOTAL.search(output)
            if mt:
                total = int(mt.group(1))
            else:
                failed = int(_FAILED.search(output).group(1)) if _FAILED.search(output) else 0
                errors = int(_ERRORS.search(output).group(1)) if _ERRORS.search(output) else 0
                total = passed + failed + errors
            return passed, max(total, passed)
        # nothing recognizable → treat as all-or-nothing on the exit code
        return (1, 1) if returncode == 0 else (0, 1)

    # custom regex
    m = re.search(mode, output)
    if not m:
        return (1, 1) if returncode == 0 else (0, 1)
    if m.lastindex and m.lastindex >= 2:
        return int(m.group(1)), max(int(m.group(2)), int(m.group(1)))
    p = int(m.group(1))
    return p, max(p, 1)


@dataclass
class VerifyResult:
    passed: int
    total: int
    returncode: int
    output: str

    @property
    def solved(self) -> bool:
        return self.total > 0 and self.passed >= self.total


@dataclass
class Verifier:
    """Runs a shell command and scores it. The command is the ground truth the
    ratchet climbs toward — the user's own tests/build/lint."""

    command: str
    mode: str = "auto"           # auto | exitcode | <custom regex>
    cwd: str | None = None
    timeout: int = 1800

    def run(self) -> VerifyResult:
        try:
            proc = subprocess.run(
                self.command, shell=True, cwd=self.cwd,
                capture_output=True, text=True, timeout=self.timeout,
            )
            out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            rc = proc.returncode
        except subprocess.TimeoutExpired as e:
            out = (e.output or "") + "\n[verifier timed out]"
            rc = 124
        p, t = score_output(out, self.mode, rc)
        return VerifyResult(p, t, rc, out)


# ─────────────────────────── checkpoint (snapshot) ───────────────────────────

def _git(args: list[str], cwd: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )


@dataclass
class GitCheckpoint:
    """Snapshot/restore the working tree via git plumbing, WITHOUT disturbing the
    user's HEAD, staging area, or branch. A snapshot is a dangling commit that
    captures tracked + untracked (non-ignored) files; restore rewrites the
    working tree to it and prunes files added since."""

    cwd: str

    def available(self) -> bool:
        r = _git(["rev-parse", "--is-inside-work-tree"], self.cwd)
        return r.returncode == 0 and r.stdout.strip() == "true"

    def _identity(self) -> dict:
        return {
            "GIT_AUTHOR_NAME": "drydock-ratchet", "GIT_AUTHOR_EMAIL": "ratchet@drydock",
            "GIT_COMMITTER_NAME": "drydock-ratchet", "GIT_COMMITTER_EMAIL": "ratchet@drydock",
        }

    def snapshot(self, label: str) -> str | None:
        """Return a commit sha capturing the current working tree, or None."""
        fd, idx = tempfile.mkstemp(prefix="ratchet-idx-")
        os.close(fd)
        try:
            os.unlink(idx)  # git wants to create it fresh
            env = {"GIT_INDEX_FILE": idx, **self._identity()}
            head = _git(["rev-parse", "HEAD"], self.cwd)
            has_head = head.returncode == 0
            if has_head:
                if _git(["read-tree", "HEAD"], self.cwd, env).returncode != 0:
                    return None
            if _git(["add", "-A"], self.cwd, env).returncode != 0:
                return None
            tree = _git(["write-tree"], self.cwd, env)
            if tree.returncode != 0:
                return None
            tree_sha = tree.stdout.strip()
            ct_args = ["commit-tree", tree_sha, "-m", label]
            if has_head:
                ct_args += ["-p", head.stdout.strip()]
            commit = _git(ct_args, self.cwd, env)
            return commit.stdout.strip() if commit.returncode == 0 else None
        finally:
            if os.path.exists(idx):
                os.unlink(idx)

    def restore(self, ref: str) -> bool:
        """Rewrite the working tree to the snapshot `ref` and remove files added
        since (so a regressed round is fully undone). Leaves .git untouched."""
        if not ref:
            return False
        fd, idx = tempfile.mkstemp(prefix="ratchet-idx-")
        os.close(fd)
        try:
            os.unlink(idx)
            env = {"GIT_INDEX_FILE": idx}
            if _git(["read-tree", ref], self.cwd, env).returncode != 0:
                return False
            # write every snapshot file back to the working tree
            if _git(["checkout-index", "-a", "-f"], self.cwd, env).returncode != 0:
                return False
            snap = set(
                _git(["ls-tree", "-r", "--name-only", ref], self.cwd).stdout.splitlines()
            )
            cur = _git(
                ["ls-files", "-o", "-c", "--exclude-standard"], self.cwd
            ).stdout.splitlines()
            for rel in cur:
                if rel and rel not in snap:
                    p = os.path.join(self.cwd, rel)
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            return True
        finally:
            if os.path.exists(idx):
                os.unlink(idx)


# ─────────────────────────── ratchet decision logic ───────────────────────────

@dataclass
class RatchetState:
    """The pawl. `best_passed` starts at the -1 sentinel so the first scored round
    always locks in (even 0/N), exactly like the reference harness."""

    goal: str
    max_rounds: int = 6
    best_passed: int = -1
    best_total: int = 0
    best_ref: str | None = None
    round: int = 0

    def record(self, passed: int, total: int, snapshot_ref: str | None) -> str:
        """Fold in a round's fitness + its workspace snapshot. Returns the action:
        'solved' | 'pawl' (improved, locked in) | 'rollback' (no gain, discard)."""
        self.round += 1
        if total > 0 and passed >= total:
            self.best_passed, self.best_total, self.best_ref = passed, total, snapshot_ref
            return "solved"
        if passed > self.best_passed:
            self.best_passed, self.best_total, self.best_ref = passed, total, snapshot_ref
            return "pawl"
        return "rollback"

    def exhausted(self) -> bool:
        return self.round >= self.max_rounds


def continuation_prompt(goal: str, passed: int, total: int) -> str:
    """Prompt for rounds ≥2: the workspace already holds prior progress; advance
    without regressing what already passes."""
    return (
        f"{goal}\n\n"
        f"[CONTINUATION — your previous work is PRESERVED in this workspace.] "
        f"A verifier currently passes {passed}/{total} of its checks. First inspect the "
        f"CURRENT state (what already works vs what's still broken), then advance: make "
        f"MORE checks pass WITHOUT breaking what already passes. Do not restart from scratch."
    )


def detect_verifier(cwd: str) -> tuple[str, str] | None:
    """Guess a verify command + fitness mode from project markers so bare
    `/ratchet <goal>` just works. Returns (command, mode) or None if nothing
    obvious is found. Most-parseable ecosystems first."""
    import json

    def here(*names: str) -> bool:
        return any(os.path.exists(os.path.join(cwd, n)) for n in names)

    if here("Cargo.toml"):
        return "cargo test", "auto"                 # "N passed; M failed"
    if here("go.mod"):
        return "go test ./...", "exitcode"          # go output isn't counted → all-or-nothing
    if here("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "setup.py", "tests", "test"):
        return "pytest -q", "auto"
    pkg = os.path.join(cwd, "package.json")
    if os.path.exists(pkg):
        try:
            scripts = (json.load(open(pkg)).get("scripts") or {})
            t = scripts.get("test", "")
            if t and "no test specified" not in t:    # skip npm's placeholder
                return "npm test --silent", "auto"
        except Exception:  # noqa: BLE001 — malformed package.json → just skip
            pass
    if here("Makefile", "makefile"):
        for mk in ("Makefile", "makefile"):
            p = os.path.join(cwd, mk)
            try:
                if os.path.exists(p) and re.search(r"^test:", open(p).read(), re.M):
                    return "make test", "exitcode"
            except OSError:
                pass
    return None


def parse_ratchet_args(arg: str) -> tuple[str, str, int, str]:
    """Parse `/ratchet <goal…> [--verify <cmd>] [--rounds N] [--fitness M]`.
    Returns (goal, verify_cmd, rounds, fitness_mode); verify_cmd and fitness_mode
    are "" when unspecified (the caller auto-detects). Raises ValueError on misuse.
    The --verify value must be a single (quoted) shell token."""
    import shlex

    try:
        toks = shlex.split(arg)
    except ValueError as e:
        raise ValueError(f"could not parse arguments: {e}") from e
    goal: list[str] = []
    verify = ""
    rounds = 6
    fitness = ""
    seen_flag = False
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "--verify":
            seen_flag = True
            i += 1
            if i >= len(toks):
                raise ValueError("--verify needs a command, e.g. --verify \"pytest -q\"")
            verify = toks[i]
        elif t == "--rounds":
            seen_flag = True
            i += 1
            if i >= len(toks) or not toks[i].isdigit():
                raise ValueError("--rounds needs a number, e.g. --rounds 6")
            rounds = max(1, min(int(toks[i]), 20))
        elif t == "--fitness":
            seen_flag = True
            i += 1
            if i >= len(toks):
                raise ValueError("--fitness needs a mode (auto|exitcode|<regex>)")
            fitness = toks[i]
        else:
            if seen_flag:
                raise ValueError(f"unexpected argument after flags: {t!r}")
            goal.append(t)
        i += 1
    if not goal:
        raise ValueError("no goal given")
    return " ".join(goal), verify, rounds, fitness
