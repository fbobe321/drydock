"""Parallel evolutionary ratchet — run λ variant attempts CONCURRENTLY per
generation, select the best, keep a Quality-Diversity archive, and cross over
complementary partial solutions. The single-incumbent /ratchet (drydock.ratchet)
hill-climbs one line of attack serially; this fans out an exploit move plus λ-1
diverse explorers at once, each in its OWN git worktree against its OWN model
server, so a hard problem is attacked from several angles per generation.

The evolutionary decisions all come from the unit-tested core in drydock.ratchet
(Archive / VariationPolicy / plan_crossover / effort_profile). This module adds
only two things: (1) a PURE generation loop (`run_eratchet`) that is agnostic to
how a variant is actually executed — the executor is injected, so the whole loop
is deterministically testable — and (2) a concurrent scheduler that assigns
variants to servers and runs them in parallel.

Isolation is by `git worktree` (one throwaway tree per variant), NOT docker — no
infra dependency, works on any machine with a git repo. Nothing here reaches the
network itself; the injected executor does (by shelling out to `drydock -p`).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from drydock.ratchet import (
    Archive,
    Candidate,
    effort_profile,
    plan_crossover,
    policy_for,
)


# ── what an executed variant reports back ──────────────────────────────────

@dataclass
class VariantOutcome:
    """The result of running one variant attempt to completion + scoring it.
    `ref` is an opaque genotype handle (a git ref the next generation can build
    from). `descriptor` is the set of passing check names (behaviour niche), or
    None when per-check info isn't available."""

    spec: dict                                   # {"mode", "temperature"}
    server: str
    passed: int
    total: int
    ref: Optional[str] = None
    descriptor: Optional[frozenset] = None
    error: str = ""


# ── a variant executor: base genotype + server + spec → outcome ─────────────
# Injected so the loop is testable. The real one (exec_variant below) makes a
# worktree, runs `drydock -p`, verifies, and snapshots.
VariantRunner = Callable[[Optional[str], str, dict, Optional[dict]], VariantOutcome]


@dataclass
class EratchetResult:
    solved: bool
    best_passed: int
    best_total: int
    best_ref: Optional[str]
    generations: int
    archive: Archive
    stopped: str = ""                            # '' | 'abort_flat' | 'budget' | 'cancelled'


def _run_generation_parallel(
    specs: list,
    servers: list,
    runner: VariantRunner,
    base_ref: Optional[str],
    xplan: Optional[dict],
) -> list:
    """Run every variant spec concurrently — one worker per available server, so
    λ variants light up all servers at once — and return the outcomes IN SPEC
    ORDER (so variant 0, the elitist exploit move, stays first). A variant that
    raises is captured as an errored outcome, never crashing the generation."""
    n = max(1, len(servers))

    def job(i: int, spec: dict) -> VariantOutcome:
        server = servers[i % n]
        plan = xplan if spec.get("mode") == "crossover" else None
        try:
            return runner(base_ref, server, spec, plan)
        except Exception as e:  # noqa: BLE001 — one variant failing != generation failing
            return VariantOutcome(spec=spec, server=server, passed=0, total=0,
                                  error=f"{type(e).__name__}: {e}")

    # at most one heavy generation per server at a time (a local model server
    # batches poorly under concurrent big requests); extra specs queue.
    with ThreadPoolExecutor(max_workers=min(len(specs), n) or 1) as ex:
        return list(ex.map(lambda p: job(*p), list(enumerate(specs))))


def run_eratchet(
    goal: str,
    *,
    servers: list,
    runner: VariantRunner,
    effort: str = "high",
    max_generations: int = 0,
    fanout: int = 0,
    on_event: Optional[Callable[[str, dict], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> EratchetResult:
    """Drive the parallel evolutionary ratchet. Pure control flow: every side
    effect (running an attempt, verifying, snapshotting) happens inside the
    injected `runner`; every progress signal goes out through `on_event`.

    Each generation: pick the variation operator (escalates on stall via the
    tested VariationPolicy), expand it to an exploit-first list of variant specs,
    run them in parallel across `servers`, fold the results into the QD archive,
    and continue from the archive's best. Stops on solved, generation budget, a
    flat (no-gradient) run, or `should_stop()`."""
    prof = effort_profile(effort)
    gens = max_generations or prof["max_rounds"]
    policy = policy_for(effort)
    if fanout:
        policy.fanout = max(1, fanout)
    abort_flat = prof["abort_flat"]

    def emit(kind: str, **data):
        if on_event:
            on_event(kind, data)

    archive = Archive()
    op = "exploit"
    base_ref: Optional[str] = None
    prev_best = -1
    flat = 0

    emit("start", goal=goal, effort=effort, generations=gens,
         fanout=policy.fanout, servers=list(servers))

    for g in range(1, gens + 1):
        if should_stop and should_stop():
            emit("cancelled", generation=g - 1)
            return EratchetResult(False, max(prev_best, 0), _best_total(archive),
                                  _best_ref(archive), g - 1, archive, "cancelled")

        specs = policy.variant_specs(op)
        xplan = None
        if op == "crossover":
            pairs = archive.complementary_pairs()
            if pairs:
                xplan = plan_crossover(pairs[0][0], pairs[0][1], g)

        emit("generation_start", generation=g, operator=op,
             variants=len(specs), modes=[s.get("mode") for s in specs])

        outcomes = _run_generation_parallel(specs, servers, runner, base_ref, xplan)

        for i, oc in enumerate(outcomes):
            if oc.error:
                emit("variant_error", generation=g, server=oc.server, error=oc.error)
                continue
            cand = Candidate(
                id=f"g{g}v{i}", fitness=oc.passed, total=oc.total,
                snapshot=oc.ref, descriptor=oc.descriptor, generation=g,
                cost=float(g),
            )
            archive.consider(cand)
            emit("variant_done", generation=g, index=i, mode=oc.spec.get("mode"),
                 server=oc.server, passed=oc.passed, total=oc.total)

        best = archive.best()
        best_passed = best.fitness if best else 0
        best_total = best.total if best else 0
        base_ref = best.snapshot if best else base_ref
        improved = best_passed > prev_best

        if best and best.solved:
            emit("solved", generation=g, passed=best_passed, total=best_total, ref=best.snapshot)
            return EratchetResult(True, best_passed, best_total, best.snapshot, g, archive)

        emit("generation_done", generation=g, operator=op, passed=best_passed,
             total=best_total, improved=improved, niches=len(archive.elites))

        # flat = no gradient at all (still 0 passing); bail before burning budget.
        flat = 0 if best_passed > 0 else flat + 1
        if abort_flat and flat >= abort_flat:
            emit("abort_flat", generation=g, total=best_total)
            return EratchetResult(False, best_passed, best_total, base_ref, g, archive, "abort_flat")

        prev_best = max(prev_best, best_passed)
        op = policy.next_operator(improved, have_crossover=bool(archive.complementary_pairs()))
        if not improved and op != "exploit":
            emit("escalate", generation=g, operator=op)

    best = archive.best()
    bp = best.fitness if best else max(prev_best, 0)
    bt = best.total if best else 0
    emit("done", generations=gens, passed=bp, total=bt, ref=best.snapshot if best else None)
    return EratchetResult(bool(best and best.solved), bp, bt,
                          best.snapshot if best else base_ref, gens, archive, "budget")


def _best_total(a: Archive) -> int:
    b = a.best()
    return b.total if b else 0


def _best_ref(a: Archive) -> Optional[str]:
    b = a.best()
    return b.snapshot if b else None


# ══════════════════════════════════════════════════════════════════════════════
# Real variant executor — the injected runner the CLI/TUI use. Isolation is a
# throwaway `git worktree`; the attempt is a headless `drydock -p` against the
# assigned server; fitness is the project verifier scored by drydock.ratchet.
# ══════════════════════════════════════════════════════════════════════════════
import os                                                      # noqa: E402
import subprocess                                              # noqa: E402
import sys                                                     # noqa: E402
import tempfile                                                # noqa: E402

from drydock.ratchet import (                                  # noqa: E402
    continuation_prompt,
    diversify_prompt,
    parse_descriptor,
    score_output,
)

# spec mode → the diversify_prompt operator that shapes the continuation.
_MODE_OP = {"continue": "exploit", "rethink": "diversify", "restart": "restart",
            "crossover": "crossover"}


def _git(args: list, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _variant_prompt(goal: str, spec: dict, progress: dict, xplan, donor_path: str) -> str:
    passed, total = progress.get("passed", 0), progress.get("total", 0)
    mode = spec.get("mode", "continue")
    if mode == "crossover" and xplan and donor_path:
        wants = ", ".join(xplan.get("wants", [])) or "the checks it passes that this tree fails"
        return (
            f"{goal}\n\n[CROSSOVER — a COMPLEMENTARY solution is checked out at "
            f"{donor_path}. It passes: {wants}. Study how it does so and fold ONLY "
            f"those parts into THIS working tree, keeping everything that already "
            f"passes here. A verifier will score the result.]"
        )
    op = _MODE_OP.get(mode, "exploit")
    if op in ("diversify", "restart"):
        return diversify_prompt(goal, passed, total, op)
    return continuation_prompt(goal, passed, total)


@dataclass
class ExecConfig:
    """Everything the real executor needs that isn't per-variant."""

    repo: str
    goal: str
    verify_cmd: str
    fitness: str = "auto"
    timeout: int = 1200                                        # per-attempt wall-clock cap
    model: str = "gemma4"
    provider: str = "vllm"
    drydock_argv: tuple = field(default_factory=lambda: (sys.executable, "-m", "drydock"))
    progress: dict = field(default_factory=lambda: {"passed": 0, "total": 0})


def _snapshot(wt: str) -> Optional[str]:
    """Commit whatever the attempt produced as a dangling commit and return its
    sha (the genotype handle). --allow-empty so a no-op attempt still yields the
    base tree as a ref."""
    _git(["add", "-A"], wt)
    _git(["-c", "user.name=drydock-erx", "-c", "user.email=erx@drydock",
          "commit", "--allow-empty", "--no-verify", "-m", "erx variant"], wt)
    r = _git(["rev-parse", "HEAD"], wt)
    return r.stdout.strip() or None


def exec_variant(cfg: ExecConfig, base_ref: Optional[str], server: str,
                 spec: dict, xplan: Optional[dict]) -> VariantOutcome:
    """Run one variant to completion in an isolated worktree and score it."""
    base = base_ref or "HEAD"
    wt = tempfile.mkdtemp(prefix="drydock-erx-")
    donor_wt = ""
    add = _git(["worktree", "add", "--detach", wt, base], cfg.repo)
    if add.returncode != 0:
        return VariantOutcome(spec, server, 0, 0, error=f"worktree add: {add.stderr.strip()}")
    try:
        if spec.get("mode") == "crossover" and xplan and xplan.get("donor"):
            donor_wt = tempfile.mkdtemp(prefix="drydock-erx-donor-")
            if _git(["worktree", "add", "--detach", donor_wt, xplan["donor"]],
                    cfg.repo).returncode != 0:
                donor_wt = ""

        prompt = _variant_prompt(cfg.goal, spec, cfg.progress, xplan, donor_wt)
        argv = [*cfg.drydock_argv, "-p", prompt, "--provider", cfg.provider,
                "--base-url", server, "--model", cfg.model,
                "--dangerously-skip-permissions"]
        temp = spec.get("temperature")
        if temp is not None:
            argv += ["--temperature", str(temp)]
        try:
            subprocess.run(argv, cwd=wt, capture_output=True, text=True, timeout=cfg.timeout)
        except subprocess.TimeoutExpired:
            pass                                              # score whatever it managed

        ref = _snapshot(wt)
        vr = subprocess.run(cfg.verify_cmd, cwd=wt, shell=True,
                            capture_output=True, text=True)
        out = (vr.stdout or "") + (vr.stderr or "")
        passed, total = score_output(out, cfg.fitness, vr.returncode)
        return VariantOutcome(spec, server, passed, total, ref=ref,
                              descriptor=parse_descriptor(out))
    finally:
        _git(["worktree", "remove", "--force", wt], cfg.repo)
        if donor_wt:
            _git(["worktree", "remove", "--force", donor_wt], cfg.repo)


def make_variant_runner(cfg: ExecConfig) -> VariantRunner:
    """Bind an ExecConfig into a VariantRunner the orchestrator can call."""
    def runner(base_ref, server, spec, xplan):
        return exec_variant(cfg, base_ref, server, spec, xplan)
    return runner


# ══════════════════════════════════════════════════════════════════════════════
# Shared front-end helpers (used by BOTH the `drydock eratchet` CLI and the TUI
# `/eratchet` launcher) — one event formatter, one tolerant parser.
# ══════════════════════════════════════════════════════════════════════════════

def format_event(kind: str, d: dict) -> str:
    """One human line per orchestrator event — identical in the CLI and the TUI."""
    g = d.get("generation")
    if kind == "start":
        return (f"⇶ eratchet: goal={d['goal']!r} · effort={d['effort']} · "
                f"{d['generations']} gens · λ≤{d['fanout']} · servers={len(d['servers'])}")
    if kind == "generation_start":
        return f"  gen {g}: {d['operator']} → {d['variants']} variant(s) {d['modes']}"
    if kind == "variant_done":
        return (f"    v{d['index']} {d['mode']}@{d['server']}: "
                f"{d['passed']}/{d['total']}")
    if kind == "variant_error":
        return f"    ✗ variant error ({d['server']}): {d['error']}"
    if kind == "generation_done":
        arrow = "↑" if d["improved"] else "•"
        return (f"  gen {g} {arrow} best {d['passed']}/{d['total']} "
                f"({d['niches']} niche(s))")
    if kind == "escalate":
        return f"  ⤴ stalled — escalating to {d['operator']}"
    if kind == "abort_flat":
        return (f"⚙ stopped at gen {g} — verifier gives no gradient (0/{d['total']}); "
                "refine the goal or --verify.")
    if kind == "solved":
        return f"🎉 SOLVED at gen {g}: {d['passed']}/{d['total']} (ref {d['ref']})"
    if kind == "cancelled":
        return "⏹ eratchet cancelled."
    if kind == "done":
        return f"⚙ eratchet done — best {d['passed']}/{d['total']}"
    return f"{kind}: {d}"


def parse_eratchet(tokens: list) -> dict:
    """Parse `<goal…> [--effort L] [--verify CMD] [--servers a,b] [--fanout N]
    [--generations N] [--model M] [--provider P] [--base-url URL]`. Raises
    ValueError with a usage-friendly message (never SystemExit — safe in the TUI)."""
    from drydock.ratchet import EFFORT_LEVELS
    goal, effort, verify, model, provider, base_url = [], "high", "", "", "", ""
    servers: list = []
    fanout = generations = 0
    i, seen_flag = 0, False
    while i < len(tokens):
        t = tokens[i]
        if t in ("--effort", "--verify", "--servers", "--fanout", "--generations",
                 "--model", "--provider", "--base-url"):
            seen_flag = True
            i += 1
            if i >= len(tokens):
                raise ValueError(f"{t} needs a value")
            v = tokens[i]
            if t == "--effort":
                if v not in EFFORT_LEVELS:
                    raise ValueError(f"--effort must be one of {'|'.join(EFFORT_LEVELS)}")
                effort = v
            elif t == "--verify":
                verify = v
            elif t == "--servers":
                servers = [s for s in v.replace(",", " ").split() if s]
            elif t == "--fanout":
                fanout = max(1, min(int(v), 16))
            elif t == "--generations":
                generations = max(1, min(int(v), 64))
            elif t == "--model":
                model = v
            elif t == "--provider":
                provider = v
            elif t == "--base-url":
                base_url = v
        elif seen_flag:
            raise ValueError(f"unexpected argument after flags: {t!r}")
        else:
            goal.append(t)
        i += 1
    if not goal:
        raise ValueError("no goal given")
    return {"goal": " ".join(goal), "effort": effort, "verify": verify,
            "servers": servers, "fanout": fanout, "generations": generations,
            "model": model, "provider": provider, "base_url": base_url}


def resolve_config(opts: dict, config: dict) -> "ExecConfig | str":
    """Turn parsed opts + drydock config into a ready ExecConfig, or an error
    string (verifier not detectable / not a git repo)."""
    from drydock.ratchet import GitCheckpoint, detect_verifier
    cwd = config.get("cwd") or os.getcwd()
    if not GitCheckpoint(cwd).available():
        return ("eratchet needs a git repo for its per-variant worktrees. "
                "Run `git init` here first.")
    verify, fitness = opts["verify"], "auto"
    if not verify:
        found = detect_verifier(cwd)
        if not found:
            return ("Couldn't auto-detect a verifier. Add --verify \"<test/build cmd>\" "
                    "(e.g. --verify \"pytest -q\").")
        verify, fitness = found
    return ExecConfig(
        repo=cwd, goal=opts["goal"], verify_cmd=verify, fitness=fitness,
        model=opts["model"] or config.get("model") or "gemma4",
        provider=opts["provider"] or config.get("provider") or "vllm",
    )


def resolve_servers(opts: dict, config: dict) -> list:
    """Server pool for the fan-out: explicit --servers, else config, else the
    single configured base_url."""
    if opts["servers"]:
        return opts["servers"]
    pool = config.get("eratchet_servers")
    if pool:
        return list(pool)
    return [opts["base_url"] or config.get("base_url") or "http://localhost:8000/v1"]


def run_cli(argv: list, config: dict | None = None) -> int:
    """`drydock eratchet …` entry point. Returns a process exit code."""
    config = config or {"cwd": os.getcwd()}
    try:
        opts = parse_eratchet(argv)
    except ValueError as e:
        print(f"eratchet: {e}\n\nusage: drydock eratchet <goal> [--effort low|medium|high|"
              "xhigh|max] [--verify \"<cmd>\"] [--servers url,url] [--fanout N] "
              "[--generations N]", file=sys.stderr)
        return 2
    cfg = resolve_config(opts, config)
    if isinstance(cfg, str):
        print(f"eratchet: {cfg}", file=sys.stderr)
        return 2
    servers = resolve_servers(opts, config)

    def on_event(kind, d):
        if kind == "generation_done":            # thread live score into prompts
            cfg.progress["passed"], cfg.progress["total"] = d["passed"], d["total"]
        print(format_event(kind, d), flush=True)

    res = run_eratchet(cfg.goal, servers=servers, runner=make_variant_runner(cfg),
                       effort=opts["effort"], max_generations=opts["generations"],
                       fanout=opts["fanout"], on_event=on_event)
    return 0 if res.solved else 1
