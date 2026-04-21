#!/usr/bin/env python3
"""Research experimenter — overnight mutate-measure-promote loop.

Random-search mutator. Reads `config_best.toml` (or `config_base.toml`
on first run), picks one mutable knob, samples a new value within its
declared bounds, writes a staged variant, shells out to `kernel.py`,
reads the metric back from the results TSV, and promotes the variant
to `config_best.toml` if its metric beats the current best.

Stops cleanly on:
  - SIGTERM / SIGINT
  - presence of a `STOP` sentinel file in this directory
  - --max-experiments N (0 = infinite; the intended overnight setting)

Never mutates:
  - drydock source code
  - kernel.py (the frozen runner)
  - config_base.toml (re-seed manually if you want a new baseline)

Call pattern (match karpathy/autoresearch's "NEVER STOP" discipline):

    python3 research/experimenter.py \\
        --results-tsv research/results.tsv \\
        --cooldown-s 10

and let it run until morning. Check `research/results.tsv` for the
full experiment log and `research/config_best.toml` for the winner.
"""
from __future__ import annotations

import argparse
import copy
import random
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import tomli_w

RESEARCH_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = RESEARCH_DIR / "results.tsv"
CONFIG_BASE = RESEARCH_DIR / "config_base.toml"
CONFIG_BEST = RESEARCH_DIR / "config_best.toml"
STAGED_DIR = RESEARCH_DIR / "staged"
STOP_SENTINEL = RESEARCH_DIR / "STOP"


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def write_toml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump(data, f)


def mutate_random(cfg: dict, rng: random.Random) -> tuple[dict, str]:
    """Pick one mutable knob and propose a new value.

    50/50 split between "sample uniformly across the full [min, max]
    range" and "nudge by ±20% of the range around the current value".
    Uniform catches distant optima; nudges refine nearby ones.
    """
    new = copy.deepcopy(cfg)
    knobs = new.get("knob", [])
    mutable_idxs = [i for i, k in enumerate(knobs)
                    if k.get("mutable", True)]
    if not mutable_idxs:
        return new, "(no mutable knobs — noop)"
    idx = rng.choice(mutable_idxs)
    k = knobs[idx]
    lo, hi = float(k["min"]), float(k["max"])
    current = float(k.get("value", k["default"]))
    if rng.random() < 0.5:
        val = rng.uniform(lo, hi)
    else:
        span = (hi - lo) * 0.2
        val = max(lo, min(hi, current + rng.uniform(-span, span)))
    if isinstance(k["default"], int):
        val = int(round(val))
    else:
        val = round(val, 3)
    k["value"] = val
    return new, f"random {k['name']}: {current} -> {val}"


def mutate_opus(cfg: dict) -> tuple[dict, str] | None:
    """Meta-Harness-style: ask Opus for a contrastive mutation.

    Imports the proposer lazily so experimenter still runs in random
    mode without the anthropic SDK installed. Returns None if the
    proposer yields NO_PROPOSAL or errors — caller falls through to
    random.
    """
    try:
        sys.path.insert(0, str(RESEARCH_DIR))
        import proposer  # type: ignore
    except ImportError as e:
        print(f"  opus mutator: proposer import failed: {e}", flush=True)
        return None
    mutation = proposer.propose(
        config_base_path=CONFIG_BASE,
        config_best_path=CONFIG_BEST if CONFIG_BEST.exists() else CONFIG_BASE,
        results_tsv=DEFAULT_RESULTS,
    )
    if mutation is None:
        return None

    new = copy.deepcopy(cfg)
    target = mutation["target"]
    name = mutation["name"]
    value = mutation["value"]
    reason = str(mutation.get("reason", ""))[:200]

    # Apply the mutation to the correct collection. Validation already
    # happened in proposer._validate_mutation; we're just rewriting.
    if target == "knob":
        for k in new.get("knob", []):
            if k["name"] == name:
                k["value"] = int(value) if isinstance(k["default"], int) else float(value)
    elif target == "harness_threshold":
        for h in new.get("harness_threshold", []):
            if h["name"] == name:
                h["value"] = int(value) if isinstance(h["default"], int) else float(value)
    elif target == "admiral_detector":
        for d in new.get("admiral_detector", []):
            if d["name"] == name:
                d["value"] = int(value) if isinstance(d["default"], int) else float(value)
    elif target == "env_flag":
        flags = new.setdefault("env_flags", {})
        flags[name] = str(value)
    else:
        return None

    desc = f"opus {target}:{name} -> {value} ({reason})"
    return new, desc


def mutate(cfg: dict, rng: random.Random, mode: str) -> tuple[dict, str]:
    """Dispatch to the requested proposer. Opus mode falls back to
    random if the proposer doesn't deliver."""
    if mode == "opus":
        r = mutate_opus(cfg)
        if r is not None:
            return r
        print("  opus mutator returned nothing; falling back to random", flush=True)
    return mutate_random(cfg, rng)


def read_metric_for(results_tsv: Path, exp_id: str) -> float:
    """Scan the TSV backwards for the metric row matching exp_id."""
    if not results_tsv.exists():
        return float("-inf")
    with results_tsv.open() as f:
        lines = f.readlines()
    if not lines:
        return float("-inf")
    header = lines[0].rstrip("\n").split("\t")
    if "exp_id" not in header or "metric" not in header:
        return float("-inf")
    exp_idx = header.index("exp_id")
    m_idx = header.index("metric")
    for line in reversed(lines[1:]):
        parts = line.rstrip("\n").split("\t")
        if len(parts) > exp_idx and parts[exp_idx] == exp_id:
            try:
                return float(parts[m_idx])
            except ValueError:
                return float("-inf")
    return float("-inf")


def current_best_metric(results_tsv: Path) -> float:
    """Overall best metric across all historical experiments."""
    if not results_tsv.exists():
        return float("-inf")
    best = float("-inf")
    try:
        with results_tsv.open() as f:
            header = f.readline().rstrip("\n").split("\t")
            if "metric" not in header:
                return best
            m_idx = header.index("metric")
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) <= m_idx:
                    continue
                try:
                    v = float(parts[m_idx])
                    if v > best:
                        best = v
                except ValueError:
                    continue
    except Exception:
        pass
    return best


def run_kernel(variant_path: Path, results_tsv: Path,
               exp_id: str, note: str) -> float:
    cmd = [
        sys.executable,
        str(RESEARCH_DIR / "kernel.py"),
        "--config", str(variant_path),
        "--results-tsv", str(results_tsv),
        "--exp-id", exp_id,
        "--note", note,
    ]
    # Kernel enforces its own 300s ceiling; wrap with 360s so the
    # experimenter never hangs if a kernel misbehaves.
    try:
        subprocess.run(cmd, timeout=360, check=False)
    except subprocess.TimeoutExpired:
        print(f"  kernel wrapper timeout — killing experiment", flush=True)
        return float("-inf")
    return read_metric_for(results_tsv, exp_id)


_stop = False


def _handle_signal(*_args) -> None:
    global _stop
    _stop = True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Random-search experimenter over drydock admiral knobs.")
    ap.add_argument("--seed", type=int, default=None,
                    help="PRNG seed for reproducibility.")
    ap.add_argument("--max-experiments", type=int, default=0,
                    help="0 = run until STOP sentinel / SIGTERM (overnight).")
    ap.add_argument("--cooldown-s", type=float, default=10.0,
                    help="Sleep between experiments (lets GPU settle).")
    ap.add_argument("--results-tsv", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--proposer", choices=("random", "opus"),
                    default="random",
                    help="random = bounded uniform + nudge sampling. "
                         "opus = Meta-Harness-style LLM proposer using "
                         "ANTHROPIC_API_KEY or the `claude` CLI; falls "
                         "back to random on any failure.")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    rng = random.Random(args.seed)

    base_cfg = load_toml(CONFIG_BASE)
    best_cfg = load_toml(CONFIG_BEST) if CONFIG_BEST.exists() else base_cfg
    best_metric = current_best_metric(args.results_tsv)

    print(f"experimenter starting | proposer={args.proposer} | "
          f"seed={args.seed} | cooldown={args.cooldown_s}s | "
          f"best_metric={best_metric:.3f}", flush=True)

    n = 0
    while not _stop:
        if STOP_SENTINEL.exists():
            print("STOP sentinel present — exiting cleanly", flush=True)
            break
        if args.max_experiments and n >= args.max_experiments:
            print(f"reached --max-experiments={args.max_experiments}",
                  flush=True)
            break
        n += 1
        exp_id = f"exp_{int(time.time())}_{n:04d}"
        variant, desc = mutate(best_cfg, rng, args.proposer)
        staged = STAGED_DIR / f"{exp_id}.toml"
        write_toml(staged, variant)

        print(f"\n[{n}] {exp_id} | mutate: {desc}", flush=True)
        metric = run_kernel(staged, args.results_tsv, exp_id, desc)
        if metric > best_metric:
            print(f"  PROMOTE: {metric:.3f} > best {best_metric:.3f}",
                  flush=True)
            shutil.copy2(staged, CONFIG_BEST)
            best_cfg = variant
            best_metric = metric
        else:
            print(f"  keep best ({best_metric:.3f} >= {metric:.3f})",
                  flush=True)

        # Small cooldown so successive kernels don't hammer vLLM.
        for _ in range(int(args.cooldown_s * 10)):
            if _stop:
                break
            time.sleep(0.1)

    print(f"experimenter stopped after {n} experiments; "
          f"final best_metric={best_metric:.3f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
