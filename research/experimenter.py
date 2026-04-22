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
import os
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


def _sample_numeric_mutation(entry: dict, rng: random.Random) -> float | int:
    """Pick a new value for a numeric-range entry (knob, harness
    threshold, admiral detector). 50/50 between uniform-over-bounds
    and ±20 % nudge around the current value."""
    lo, hi = float(entry["min"]), float(entry["max"])
    current = float(entry.get("value", entry["default"]))
    if rng.random() < 0.5:
        val = rng.uniform(lo, hi)
    else:
        span = (hi - lo) * 0.2
        val = max(lo, min(hi, current + rng.uniform(-span, span)))
    if isinstance(entry.get("default"), int):
        return int(round(val))
    return round(val, 3)


def mutate_random(cfg: dict, rng: random.Random,
                  exclude_target: str | None = None) -> tuple[dict, str]:
    """Pick one mutable entry across ALL classes (knob, harness_threshold,
    admiral_detector, env_flag) and propose a change.

    Previous version only sampled from `knob` — when mandatory rotation
    rejected an LLM proposer's knob mutation, random fallback picked
    another knob and nothing actually shifted. Now random picks a class
    first (uniform), then an entry within it. Honors exclude_target so
    the rotation caller can force diversification away from the
    dominant class.

    Prompts are intentionally NOT in the random surface — meaningful
    prompt text can't be randomly sampled. LLM-only for that target.
    """
    new = copy.deepcopy(cfg)
    # Build (target_class, entry) candidates
    candidates: list[tuple[str, object]] = []
    if exclude_target != "knob":
        for k in new.get("knob", []):
            if k.get("mutable", True):
                candidates.append(("knob", k))
    if exclude_target != "harness_threshold":
        for h in new.get("harness_threshold", []):
            if h.get("mutable", True):
                candidates.append(("harness_threshold", h))
    if exclude_target != "admiral_detector":
        for d in new.get("admiral_detector", []):
            if d.get("mutable", True):
                candidates.append(("admiral_detector", d))
    if exclude_target != "env_flag":
        for name in (new.get("env_flags") or {}):
            candidates.append(("env_flag", name))
    if not candidates:
        return new, "(no mutable targets — noop)"

    # Uniform over candidates, NOT over classes. A class with more
    # mutable entries gets proportionally more picks. Good: matches
    # the declared surface. Bad: dominated by whichever section has
    # the most entries. Counterbalanced by exclude_target when
    # rotation is active.
    target_class, entry = rng.choice(candidates)

    if target_class == "env_flag":
        # Binary flip on the current value. DRYDOCK_AUTO_CONTINUE_
        # DISABLE is the only one we currently declare; it's "0" or
        # "1". Generic flip is str("0" ↔ "1").
        name = entry  # type: ignore
        flags = new.setdefault("env_flags", {})
        current = flags.get(name, "0")
        new_value = "0" if current == "1" else "1"
        flags[name] = new_value
        return new, (f"random env_flag:{name}: "
                     f"{current!r} -> {new_value!r}")

    # Numeric mutation for knob / harness_threshold / admiral_detector
    entry_dict = entry  # type: ignore
    current = entry_dict.get("value", entry_dict["default"])
    val = _sample_numeric_mutation(entry_dict, rng)
    entry_dict["value"] = val
    return new, (f"random {target_class}:{entry_dict['name']}: "
                 f"{current} -> {val}")


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
    elif target == "prompt":
        # Materialize the proposed prompt to a candidate-local file and
        # point the variant's source_path at it. The kernel will copy
        # this into the isolated HOME's DRYDOCK_PROMPTS_DIR at spawn.
        # Staging dir mirrors the variant toml: one sibling per exp.
        # Using a stable-per-variant name (no exp_id here yet — caller
        # provides it via the outer staged path). We write into the
        # per-variant staged_prompts subdir and let the kernel resolve.
        prompts = new.setdefault("prompts", {})
        prompt_entry = prompts.setdefault(name, {})
        prompts_dir = STAGED_DIR / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        # Use a content-hash so concurrent / retried variants don't
        # stomp each other. Kernel reads the file by source_path.
        import hashlib as _hashlib
        h = _hashlib.sha1(str(value).encode()).hexdigest()[:12]
        target_file = prompts_dir / f"{name}_{h}.md"
        target_file.write_text(str(value))
        prompt_entry["source_path"] = str(target_file)
        prompt_entry["mutable"] = True
    else:
        return None

    # Label says "llm" to avoid implying we called Opus even in local
    # mode (proposer transport chooses local-first when ALLOW_OPUS=0).
    # Keeps the results.tsv "note" column truthful when read later.
    if target == "prompt":
        size = len(str(value))
        desc = f"llm prompt:{name} -> [{size} chars] ({reason})"
    else:
        desc = f"llm {target}:{name} -> {value} ({reason})"
    return new, desc


def mutate(cfg: dict, rng: random.Random, mode: str) -> tuple[dict, str]:
    """Dispatch to the requested proposer. LLM modes fall back to
    random if the proposer doesn't deliver.

    `local` and `opus` share the same proposer code; the difference is
    the transport (proposer.py decides based on
    DRYDOCK_RESEARCH_ALLOW_OPUS). `opus` only contacts the cloud if
    the operator explicitly set the env var; otherwise it behaves like
    `local` (which calls the on-box vLLM endpoint).

    When the LLM proposer is rejected by mandatory rotation (because a
    single target class has dominated recent coverage), the fallback
    random mutator excludes that same class — so fallback actually
    diversifies coverage instead of picking yet another knob.
    """
    if mode in ("opus", "local"):
        if mode == "opus":
            os.environ["DRYDOCK_RESEARCH_ALLOW_OPUS"] = "1"
        else:
            os.environ["DRYDOCK_RESEARCH_ALLOW_OPUS"] = "0"
        r = mutate_opus(cfg)  # function name is historical; contract unchanged
        if r is not None:
            return r
        print(f"  {mode} mutator returned nothing; falling back to random",
              flush=True)

    # Compute rotation exclusion if a class dominates recent coverage.
    exclude = _dominant_recent_class()
    if exclude:
        print(f"  random mutator: excluding dominant class '{exclude}' "
              f"to diversify coverage", flush=True)
    return mutate_random(cfg, rng, exclude_target=exclude)


def _dominant_recent_class() -> str | None:
    """Mirror of proposer._recent_mutation_coverage's logic: if a
    single class has ≥70 % of the last 15 experiments, return its
    name. Used by the random fallback to exclude it."""
    try:
        sys.path.insert(0, str(RESEARCH_DIR))
        import proposer  # type: ignore
    except ImportError:
        return None
    coverage = proposer._recent_mutation_coverage(
        RESEARCH_DIR / "results.tsv", n=15)
    total = sum(coverage.values())
    if total < 10:
        return None
    if not coverage:
        return None
    dominant = max(coverage, key=lambda k: coverage[k])
    if coverage[dominant] / total >= 0.7:
        return dominant
    return None


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
    ap.add_argument("--proposer", choices=("random", "local", "opus"),
                    default="local",
                    help="local = Meta-Harness LLM proposer against the "
                         "local vLLM endpoint (drydock's own model). "
                         "THIS IS THE DEFAULT. Keeps the self-tuning loop "
                         "air-gap-safe — matches the 'self-hosted agents "
                         "for regulated environments' positioning. "
                         "random = bounded uniform + nudge sampling, no "
                         "LLM involved. "
                         "opus = cloud proposer via ANTHROPIC_API_KEY or "
                         "the `claude` CLI — NOT AIRGAP-SAFE; only use "
                         "for development when you want a smarter "
                         "reasoner. Also requires setting env "
                         "DRYDOCK_RESEARCH_ALLOW_OPUS=1 to actually "
                         "dispatch cloud calls. Falls back to random "
                         "otherwise.")
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

        if metric <= best_metric:
            # Didn't beat current best on first run — don't bother
            # replicating a likely-mediocre config.
            print(f"  keep best ({best_metric:.3f} >= {metric:.3f})",
                  flush=True)
        else:
            # First run beat current best. Before promoting, replicate
            # twice more and take the median. The metric cliff (>50%
            # failure → 0.0) produces noisy single-samples that
            # previously promoted lucky configs and stayed there. The
            # plateau at 4.725 on the local run was partially from
            # one-shot noise: recent exps on the same proposal scored
            # 0.000 / 4.151 / 4.609 — same config, wildly different
            # outcomes. Median-of-3 is the cheapest filter.
            print(f"  R1 beat best ({metric:.3f} > {best_metric:.3f}); "
                  f"replicating to confirm", flush=True)
            replicates = [metric]
            for rep in (2, 3):
                if _stop or STOP_SENTINEL.exists():
                    break
                # Brief cooldown between replicates.
                for _ in range(int(args.cooldown_s * 10)):
                    if _stop:
                        break
                    time.sleep(0.1)
                if _stop:
                    break
                rep_exp_id = f"{exp_id}_rep{rep}"
                rep_metric = run_kernel(staged, args.results_tsv,
                                        rep_exp_id, f"{desc} [rep{rep}]")
                replicates.append(rep_metric)
                print(f"    R{rep}: {rep_metric:.3f} "
                      f"(replicates so far: {replicates})",
                      flush=True)
            if len(replicates) >= 2:
                sorted_reps = sorted(replicates)
                median = sorted_reps[len(sorted_reps) // 2]
            else:
                median = replicates[0]
            if median > best_metric:
                print(f"  PROMOTE: median {median:.3f} > best "
                      f"{best_metric:.3f} across {len(replicates)} runs",
                      flush=True)
                shutil.copy2(staged, CONFIG_BEST)
                best_cfg = variant
                best_metric = median
            else:
                print(f"  HOLD: R1 was lucky. median {median:.3f} "
                      f"across {len(replicates)} runs ≤ best "
                      f"{best_metric:.3f}", flush=True)

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
