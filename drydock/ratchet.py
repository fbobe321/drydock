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
from dataclasses import dataclass, field


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
                mf = _FAILED.search(output)
                me = _ERRORS.search(output)
                failed = int(mf.group(1)) if mf else 0
                errors = int(me.group(1)) if me else 0
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


def parse_ratchet_args(arg: str) -> tuple[str, str, int, str, str]:
    """Parse `/ratchet <goal…> [--verify <cmd>] [--effort L] [--rounds N] [--fitness M]`.
    Returns (goal, verify_cmd, rounds, fitness_mode, effort). rounds is 0 when
    unspecified (the caller derives it from effort); verify_cmd/fitness_mode/effort
    are "" when unspecified. --effort ∈ low|medium|high|xhigh|max scales the whole
    thing from a plain pawl (low) to full evolutionary search (high+)."""
    import shlex

    try:
        toks = shlex.split(arg)
    except ValueError as e:
        raise ValueError(f"could not parse arguments: {e}") from e
    goal: list[str] = []
    verify = ""
    rounds = 0
    fitness = ""
    effort = ""
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
            rounds = max(1, min(int(toks[i]), 30))
        elif t == "--effort":
            seen_flag = True
            i += 1
            if i >= len(toks) or toks[i] not in EFFORT_LEVELS:
                raise ValueError(f"--effort must be one of {'|'.join(EFFORT_LEVELS)}")
            effort = toks[i]
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
    return " ".join(goal), verify, rounds, fitness, effort


# ═══════════════════════════════════════════════════════════════════════════════
# Evolutionary core — lift the (1+1) hill-climber into a population-based search:
#   Information  → Candidate (genotype ref + lineage + fitness history)
#   Replication  → Archive keeps diverse elites (not one incumbent)
#   Variation    → VariationPolicy escalates the operator on stall; crossover splices
#   Selection    → Pareto (multi-objective) + Quality-Diversity niching
# Pure/stdlib and unit-tested; the ratchet driver wires genotypes (git/docker refs).
# ═══════════════════════════════════════════════════════════════════════════════

# ── per-check behaviour descriptor (which checks pass, not just how many) ──

def parse_descriptor(output: str) -> frozenset[str] | None:
    """Extract the SET of passing check names from verbose test output — the
    'behaviour descriptor' that lets the archive niche by *what* passes, not just
    the count. Handles pytest -v ("path::test PASSED") and unittest -v
    ("test_x (mod) ... ok"). Returns None if it can't identify individual checks."""
    names = set(re.findall(r"([\w./]+::[\w\[\].-]+)\s+PASSED", output))
    if not names:
        names = {m.group(1) for m in re.finditer(r"^(test\w+)\b.*\bok\s*$", output, re.M)}
    return frozenset(names) if names else None


# ── Information: a candidate with lineage (rec 4) ──

@dataclass
class Candidate:
    """One solution in the population. `descriptor` is the behaviour niche (set of
    passing checks); when unknown it falls back to a pass-count bucket so the
    archive still functions. `snapshot` is the opaque genotype handle (a git or
    docker ref the driver can restore)."""

    id: str
    fitness: int                              # primary objective: passing checks
    total: int
    snapshot: str | None = None               # genotype handle
    descriptor: frozenset[str] | None = None  # which checks pass (behaviour)
    parents: tuple[str, ...] = ()
    generation: int = 0
    cost: float = 0.0                          # rounds/tokens spent (minimize)
    generalizes: float = 0.0                   # held-out proxy in [0,1] (maximize)
    history: tuple[int, ...] = ()              # fitness trajectory

    @property
    def solved(self) -> bool:
        return self.total > 0 and self.fitness >= self.total

    @property
    def niche(self):
        """Key for Quality-Diversity binning: the behaviour descriptor, or a
        pass-count bucket when per-check info isn't available."""
        return self.descriptor if self.descriptor is not None else ("count", self.fitness)

    def objectives(self) -> tuple[float, float, float]:
        """Maximize all three: (checks passed, generalization, -cost)."""
        return (float(self.fitness), float(self.generalizes), -float(self.cost))


# ── Selection: multi-objective / Pareto (rec 6) ──

def dominates(a: Candidate, b: Candidate) -> bool:
    """Pareto dominance: a is ≥ b on every objective and strictly > on at least one."""
    ao, bo = a.objectives(), b.objectives()
    return all(x >= y for x, y in zip(ao, bo)) and any(x > y for x, y in zip(ao, bo))


def pareto_front(cands: list[Candidate]) -> list[Candidate]:
    """The non-dominated set — solutions no other solution beats on all objectives."""
    return [c for c in cands if not any(dominates(o, c) for o in cands if o is not c)]


# ── Replication + Quality-Diversity archive (rec 1) ──

@dataclass
class Archive:
    """Quality-Diversity population: one elite per behaviour niche, so diversity a
    single incumbent would crush is preserved. Replaces the ratchet's single best
    snapshot; the global best is still available via best()."""

    elites: dict = field(default_factory=dict)   # niche -> Candidate

    def consider(self, c: Candidate) -> str:
        """Offer a candidate. Returns 'new-niche' | 'improved' | 'rejected'.
        Within a niche the higher fitness (tie-broken by fewer parents/cost) wins."""
        cur = self.elites.get(c.niche)
        if cur is None:
            self.elites[c.niche] = c
            return "new-niche"
        if (c.fitness, c.generalizes, -c.cost) > (cur.fitness, cur.generalizes, -cur.cost):
            self.elites[c.niche] = c
            return "improved"
        return "rejected"

    def best(self) -> Candidate | None:
        if not self.elites:
            return None
        return max(self.elites.values(), key=lambda c: (c.fitness, c.generalizes, -c.cost))

    def all(self) -> list[Candidate]:
        return list(self.elites.values())

    def solved(self) -> Candidate | None:
        return next((c for c in self.elites.values() if c.solved), None)

    def complementary_pairs(self) -> list[tuple[Candidate, Candidate]]:
        """Pairs whose passing-checks UNION strictly exceeds either alone — the
        raw material for crossover. Only meaningful with per-check descriptors."""
        pool = [c for c in self.elites.values() if c.descriptor]
        pairs = []
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                a, b = pool[i], pool[j]
                union = a.descriptor | b.descriptor
                if len(union) > max(len(a.descriptor), len(b.descriptor)):
                    pairs.append((a, b))
        # most promising first: largest reachable union
        pairs.sort(key=lambda p: len(p[0].descriptor | p[1].descriptor), reverse=True)
        return pairs


# ── Variation: crossover of partial solutions (rec 2) ──

def plan_crossover(a: Candidate, b: Candidate, gen: int) -> dict | None:
    """Given two candidates passing complementary checks, describe an offspring that
    should pass their union. Returns a plan the driver executes (splice b's edits
    for the checks a lacks onto a's genotype, then verify). None if not complementary."""
    if not (a.descriptor and b.descriptor):
        return None
    union = a.descriptor | b.descriptor
    if len(union) <= max(len(a.descriptor), len(b.descriptor)):
        return None
    base, donor = (a, b) if len(a.descriptor) >= len(b.descriptor) else (b, a)
    return {
        "base": base.snapshot,                       # start from the stronger parent
        "donor": donor.snapshot,                     # graft the missing capabilities
        # descriptors are guaranteed non-empty by the guard above; the `or`
        # keeps the type checker happy across the base/donor reassignment.
        "wants": sorted((donor.descriptor or frozenset()) - (base.descriptor or frozenset())),  # checks to import
        "target": sorted(union),
        "parents": (a.id, b.id),
        "generation": gen,
    }


# ── Variation: escalate the operator on stall (rec 3) ──

class VariationPolicy:
    """Turns the (1+1) climber into (1+λ) with diversification. Each round returns
    the operator to use; on repeated no-improvement it escalates:
      exploit → diversify → fan-out(λ) → crossover → restart-elite → (loop)."""

    LADDER = ("exploit", "diversify", "fanout", "crossover", "restart")

    def __init__(self, patience: int = 2, fanout: int = 3, ladder: tuple | None = None):
        # patience default 2: give the steady pawl a couple of rounds to grind
        # before spending compute on exploration (patience=1 escalated too fast).
        # `ladder` sets how FAR the policy may escalate — the effort dial truncates
        # it (low = ("exploit",) → a pure hill-climber == the plain ratchet).
        self.patience = max(1, patience)   # stalls tolerated before escalating
        self.fanout = max(1, fanout)
        self.ladder = tuple(ladder) if ladder else self.LADDER
        self.stall = 0
        self.rung = 0

    def next_operator(self, improved: bool, have_crossover: bool = False) -> str:
        """Advance the policy given last round's outcome; return the next operator."""
        if improved:
            self.stall = 0
            self.rung = 0
            return "exploit"
        self.stall += 1
        if self.stall >= self.patience:
            self.stall = 0
            self.rung = min(self.rung + 1, len(self.ladder) - 1)
        op = self.ladder[self.rung]
        if op == "crossover" and not have_crossover:
            op = "restart" if "restart" in self.ladder else "exploit"
        return op

    def variant_specs(self, op: str) -> list[dict]:
        """Per-variant (mode, temperature) attempts for one round. ELITISM: the
        FIRST variant is ALWAYS the steady low-temp exploit move, so a round can
        never score below the plain pawl; any extra variants only EXPLORE on top.
        The driver runs them from the same best snapshot and keeps the best — which
        makes the eratchet a strict superset of the plain ratchet."""
        exploit = {"mode": "continue", "temperature": 0.2}
        # fan-out width scales with self.fanout (the effort dial): fanout total
        # attempts = 1 exploit + (fanout-1) explorers.
        pool = [{"mode": "continue", "temperature": 0.5},
                {"mode": "rethink", "temperature": 0.7},
                {"mode": "continue", "temperature": 0.6},
                {"mode": "rethink", "temperature": 0.85}]
        fan = [(pool * 4)[i] for i in range(max(0, self.fanout - 1))]
        extra = {
            "exploit":   [],
            "diversify": [{"mode": "rethink", "temperature": 0.6}],
            "fanout":    fan,
            "crossover": [{"mode": "crossover", "temperature": 0.4}],
            "restart":   [{"mode": "restart", "temperature": 0.8}],
        }.get(op, [])
        return [exploit, *extra]

    def params_for(self, op: str) -> dict:
        """Knobs the driver applies: per-variant specs (exploit-first), variant
        count, and the primary mode (the exploit move)."""
        specs = self.variant_specs(op)
        return {"variants": len(specs), "specs": specs, "mode": specs[0]["mode"]}


# ── the effort spectrum (one dial: plain pawl → full evolutionary) ──

_FULL_LADDER = ("exploit", "diversify", "fanout", "crossover", "restart")

# low = a pure hill-climber (the plain ratchet); each step up spends more compute
# and unlocks more of the evolutionary ladder. `abort_flat` = rounds to tolerate a
# 0/N no-gradient task before bailing (0 = never bail).
_EFFORT = {
    "low":    {"max_rounds": 3,  "patience": 2, "fanout": 1, "ladder": ("exploit",),                 "abort_flat": 1},
    "medium": {"max_rounds": 5,  "patience": 2, "fanout": 2, "ladder": ("exploit", "diversify"),      "abort_flat": 2},
    "high":   {"max_rounds": 8,  "patience": 2, "fanout": 3, "ladder": _FULL_LADDER,                   "abort_flat": 3},
    "xhigh":  {"max_rounds": 12, "patience": 3, "fanout": 4, "ladder": _FULL_LADDER,                   "abort_flat": 4},
    "max":    {"max_rounds": 16, "patience": 3, "fanout": 5, "ladder": _FULL_LADDER,                   "abort_flat": 0},
}
EFFORT_LEVELS = tuple(_EFFORT)


def effort_profile(level: str) -> dict:
    """Map an effort level (low|medium|high|xhigh|max) to ratchet knobs. Unknown →
    'high'. `low` is exactly the plain pawl (exploit-only, no exploration)."""
    return dict(_EFFORT.get(level, _EFFORT["high"]))


def policy_for(level: str) -> VariationPolicy:
    """A VariationPolicy pre-configured for an effort level."""
    p = effort_profile(level)
    return VariationPolicy(patience=p["patience"], fanout=p["fanout"], ladder=p["ladder"])


def diversify_prompt(goal: str, passed: int, total: int, op: str) -> str:
    """Operator-specific continuation prompt so a stalled round actually varies its
    approach instead of repeating the same failing move."""
    base = (
        f"{goal}\n\n[CONTINUATION — your previous work is PRESERVED.] A verifier passes "
        f"{passed}/{total}. "
    )
    if op == "diversify":
        return base + (
            "Your last approach STALLED. Do NOT repeat it — step back and try a "
            "DIFFERENT strategy for the still-failing checks: re-read the failing "
            "area, question an assumption you made, and take another route. Keep "
            "everything that already passes."
        )
    if op == "restart":
        return base + (
            "Progress is stuck. Reconsider the problem from first principles for the "
            "remaining checks while preserving what passes — a fresh decomposition."
        )
    return continuation_prompt(goal, passed, total)
