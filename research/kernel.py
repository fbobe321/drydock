#!/usr/bin/env python3
"""Research kernel — fixed 5-minute stress runner.

Contract:
  - Isolated HOME + cwd (tmpdir per invocation) so concurrent kernels or
    a running stress harness don't collide on session dirs / configs.
  - Reads a variant TOML (see research/config_base.toml for schema).
  - Writes ~/.drydock/admiral_tuning.json inside the isolated HOME with
    the knob values from the variant. Admiral's built-in KNOB_BOUNDS
    clips malformed values; we also pre-validate here so bugs surface
    before spawn.
  - Spawns drydock, runs research/mini_prompts.txt with a HARD 5-min
    ceiling, captures done/skip/timeout counts.
  - Appends one TSV row to --results-tsv. Never deletes or rewrites
    history; append-only.

This is the "frozen kernel" half of the autoresearch split. The other
half (experimenter.py) mutates the variant config and calls this. Keep
the kernel deterministic enough that the same config hash produces
similar metrics; report variance if you see it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pexpect

# Reuse the harness primitives. Shakedown_interactive reads SESSION_ROOT
# from ~/.drydock/config.toml at import, so we have to fix it up after
# import to point at the isolated HOME's session dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import shakedown_interactive  # noqa: E402
from shakedown_interactive import (  # noqa: E402
    DRYDOCK_BIN,
    SessionWatcher,
    drain_pty,
    send_prompt_and_confirm,
)

KERNEL_BUDGET_SEC = 300   # hard ceiling per experiment
MAX_PER_PROMPT_SEC = 45   # each kernel prompt should complete fast
IDLE_QUIET_SEC = 4        # no new messages for this long = prompt done
RESEARCH_DIR = Path(__file__).resolve().parent


def load_variant(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def config_sha(cfg: dict) -> str:
    blob = json.dumps(cfg, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:10]


def validate_and_collect_knobs(cfg: dict) -> dict[str, float]:
    """Return {knob_name: value}, pre-clipped to the variant's own bounds.
    Admiral will clip again at load time, but we prefer failing loud here
    if a variant puts a value outside its declared [min, max]."""
    out: dict[str, float] = {}
    for k in cfg.get("knob", []):
        name = k["name"]
        lo = float(k["min"])
        hi = float(k["max"])
        val = float(k.get("value", k["default"]))
        if not (lo <= val <= hi):
            raise ValueError(
                f"knob {name}={val} outside variant bounds [{lo}, {hi}]"
            )
        out[name] = val
    return out


def setup_isolated_home(tmpdir: Path, cfg: dict) -> tuple[Path, Path, Path]:
    """Create tmpdir/home (drydock reads config from here) and
    tmpdir/cwd (drydock builds the package here). Returns (home, cwd,
    session_root)."""
    home = tmpdir / "home"
    cwd = tmpdir / "cwd"
    session_root = home / ".vibe" / "logs" / "session"
    session_root.mkdir(parents=True)
    drydock_dir = home / ".drydock"
    drydock_dir.mkdir(parents=True)
    cwd.mkdir(parents=True)

    # Drydock config — point session_logging at the isolated session dir.
    # Everything else falls back to drydock defaults.
    (drydock_dir / "config.toml").write_text(
        '[session_logging]\n'
        f'save_dir = "{session_root}"\n'
        'session_prefix = "session"\n'
        'enabled = true\n'
    )

    # Admiral tuning — write variant knobs keyed on (model, task).
    knobs = validate_and_collect_knobs(cfg)
    target = cfg.get("target", {})
    model = target.get("model", "mistral-vibe-cli-latest")
    task = target.get("task", "unknown")
    tuning = {f"{model}+{task}": knobs}
    (drydock_dir / "admiral_tuning.json").write_text(json.dumps(tuning))

    # Seed cwd with the mini PRD. Harness's fresh-start wipe keeps these.
    mini_prd = (RESEARCH_DIR / "mini_prd.md").read_text()
    (cwd / "PRD.master.md").write_text(mini_prd)
    (cwd / "PRD.md").write_text(mini_prd)
    return home, cwd, session_root


def parse_prompts(path: Path) -> list[str]:
    items: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items


def run_kernel(variant_path: Path, results_tsv: Path,
               exp_id: str, note: str) -> tuple[float, dict]:
    cfg = load_variant(variant_path)
    sha = config_sha(cfg)
    prompts = parse_prompts(RESEARCH_DIR / "mini_prompts.txt")

    tmpdir = Path(f"/tmp/research_{exp_id}")
    if tmpdir.exists():
        shutil.rmtree(tmpdir)
    home, cwd, session_root = setup_isolated_home(tmpdir, cfg)

    # The imported shakedown_interactive already resolved SESSION_ROOT
    # to the user's real ~/.vibe/logs/session. Point it at the isolated
    # one so SessionWatcher finds THIS kernel's session.
    shakedown_interactive.SESSION_ROOT = session_root

    env = {
        **os.environ,
        "HOME": str(home),
        "TERM": "xterm-256color",
        "COLUMNS": "120",
        "LINES": "40",
    }
    for k, v in (cfg.get("env_flags") or {}).items():
        env[str(k)] = str(v)

    # Translate variant TOML sections into DRYDOCK_* env overrides the
    # harness + agent_loop read at startup. Admiral's KNOB_BOUNDS path
    # still writes to admiral_tuning.json (see validate_and_collect_knobs
    # above) because those tunables flow through a different apply hook;
    # these env overrides cover the constants that live in scripts/ and
    # drydock/core/ sources, not in admiral's tuning store.
    for h in (cfg.get("harness_threshold") or []):
        name = h.get("name")
        val = h.get("value", h.get("default"))
        if name and val is not None:
            env[f"DRYDOCK_STRESS_{name}"] = str(val)
    for d in (cfg.get("admiral_detector") or []):
        name = d.get("name")
        val = d.get("value", d.get("default"))
        if name and val is not None:
            env[f"DRYDOCK_ADMIRAL_{name}"] = str(val)

    log_path = tmpdir / "tui.log"
    start = time.time()

    counts = {"done": 0, "skipped": 0, "timed_out": 0, "recycles": 0,
              "prompts_attempted": 0}

    child = pexpect.spawn(DRYDOCK_BIN, encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(cwd))
    child.logfile_read = open(log_path, "w", buffering=1)
    try:
        child.expect([r">", r"Drydock", r"┌"], timeout=30)
        time.sleep(2)
        try:
            if "Trust this folder" in (child.before or ""):
                child.send("\x1b[D")
                time.sleep(0.3)
                child.send("\r")
                time.sleep(2)
        except Exception:
            pass

        watcher = SessionWatcher(cwd, since=start)
        for _ in range(30):
            watcher.refresh()
            if watcher.session_dir is not None:
                break
            time.sleep(1)

        for prompt in prompts:
            elapsed = time.time() - start
            if elapsed > KERNEL_BUDGET_SEC - 15:
                break  # leave 15s slack before the 5-min hard cap
            counts["prompts_attempted"] += 1
            prev_msgs = watcher.refresh()
            ok = send_prompt_and_confirm(child, prompt, watcher,
                                         max_retries=2, wait_per_retry=20.0)
            if not ok:
                counts["skipped"] += 1
                continue

            deadline = time.time() + min(
                MAX_PER_PROMPT_SEC,
                KERNEL_BUDGET_SEC - (time.time() - start) - 5,
            )
            last_change = time.time()
            last_count = prev_msgs
            while time.time() < deadline:
                drain_pty(child, seconds=1.0)
                cur = watcher.refresh()
                if cur > last_count:
                    last_count = cur
                    last_change = time.time()
                elif ((time.time() - last_change) > IDLE_QUIET_SEC
                        and cur > prev_msgs):
                    break
                time.sleep(0.3)
            if time.time() >= deadline and last_count <= prev_msgs:
                counts["timed_out"] += 1
            else:
                counts["done"] += 1
    finally:
        try:
            child.sendcontrol("c")
            time.sleep(0.4)
            if child.isalive():
                child.terminate(force=True)
        except Exception:
            pass
        try:
            lf = getattr(child, "logfile_read", None)
            if lf and hasattr(lf, "close"):
                lf.close()
        except Exception:
            pass

    elapsed = time.time() - start
    # Metric: done per minute, with a cliff at >50% skip+timeout rate.
    # The cliff exists because a config that "completes fast" by refusing
    # most prompts should not outscore a slower config that accepts them.
    elapsed_min = max(elapsed / 60.0, 1e-6)
    attempts = max(
        counts["done"] + counts["skipped"] + counts["timed_out"], 1)
    failure_rate = (counts["skipped"] + counts["timed_out"]) / attempts
    metric = counts["done"] / elapsed_min if failure_rate < 0.5 else 0.0

    try:
        git_commit = subprocess.check_output(
            ["git", "-C", "/data3/drydock", "rev-parse", "--short", "HEAD"],
            text=True, timeout=5).strip()
    except Exception:
        git_commit = "unknown"

    # --- Trace capture ---
    # Every kernel run leaves a reproducible artifact dir so the Meta-
    # Harness proposer can read both what happened (messages, tui log)
    # and the resulting score. Traces are the proposer's primary input
    # when forming contrastive context (top-3 vs bottom-3).
    _capture_trace(
        exp_id=exp_id,
        watcher_session_dir=watcher.session_dir if watcher else None,
        tui_log_path=log_path,
        config_sha=sha,
        git_commit=git_commit,
        metric=metric,
        counts=counts,
        elapsed=elapsed,
        note=note,
    )

    results_tsv.parent.mkdir(parents=True, exist_ok=True)
    if not results_tsv.exists():
        results_tsv.write_text(
            "ts\texp_id\tgit_commit\tconfig_sha\tmetric\tdone\t"
            "skip\ttimeout\trecycle\telapsed_s\tnote\n"
        )
    row = [
        str(int(time.time())),
        exp_id,
        git_commit,
        sha,
        f"{metric:.3f}",
        str(counts["done"]),
        str(counts["skipped"]),
        str(counts["timed_out"]),
        str(counts["recycles"]),
        f"{elapsed:.1f}",
        (note or "").replace("\t", " ").replace("\n", " ")[:120],
    ]
    with results_tsv.open("a") as f:
        f.write("\t".join(row) + "\n")

    return metric, {**counts, "elapsed_s": elapsed, "config_sha": sha}


def _capture_trace(*, exp_id: str,
                   watcher_session_dir: Path | None,
                   tui_log_path: Path,
                   config_sha: str,
                   git_commit: str,
                   metric: float,
                   counts: dict,
                   elapsed: float,
                   note: str) -> None:
    """Write research/traces/<exp_id>/ with the artifacts a proposer
    needs to reason about this run. Never raises — trace capture is
    best-effort instrumentation, not part of the metric path.
    """
    try:
        trace_dir = RESEARCH_DIR / "traces" / exp_id
        trace_dir.mkdir(parents=True, exist_ok=True)

        # 1. messages.jsonl — the drydock session log (copy, don't link;
        #    session dirs get garbage-collected).
        if watcher_session_dir and watcher_session_dir.is_dir():
            src_msgs = watcher_session_dir / "messages.jsonl"
            if src_msgs.is_file():
                try:
                    (trace_dir / "messages.jsonl").write_bytes(src_msgs.read_bytes())
                except OSError:
                    pass
            src_meta = watcher_session_dir / "meta.json"
            if src_meta.is_file():
                try:
                    (trace_dir / "meta.json").write_bytes(src_meta.read_bytes())
                except OSError:
                    pass

        # 2. tui.log — full pexpect PTY stream (may be large but the
        #    proposer can tail it).
        try:
            if tui_log_path.is_file():
                (trace_dir / "tui.log").write_bytes(tui_log_path.read_bytes())
        except OSError:
            pass

        # 3. rec_check.jsonl — extract the per-iteration diagnostic
        #    lines from the kernel's own stdout capture. Useful for
        #    spotting banner / raw-md patterns without re-parsing the
        #    big TUI log.
        try:
            rec_lines: list[dict] = []
            if tui_log_path.is_file():
                data = tui_log_path.read_text(errors="replace")
                for line in data.splitlines():
                    m = re.search(
                        r"\[rec-check\]\s+banner=(\S+)\s+log_size=(\d+)"
                        r"(?:\s+raw_md=(\d+))?",
                        line,
                    )
                    if m:
                        rec_lines.append({
                            "banner": m.group(1),
                            "log_size": int(m.group(2)),
                            "raw_md": int(m.group(3)) if m.group(3) else 0,
                        })
            with (trace_dir / "rec_check.jsonl").open("w") as f:
                for r in rec_lines:
                    f.write(json.dumps(r) + "\n")
        except OSError:
            pass

        # 4. summary.json — everything the proposer cares about at a
        #    glance.
        summary = {
            "exp_id": exp_id,
            "ts": int(time.time()),
            "git_commit": git_commit,
            "config_sha": config_sha,
            "metric": round(float(metric), 4),
            "done": counts.get("done", 0),
            "skipped": counts.get("skipped", 0),
            "timed_out": counts.get("timed_out", 0),
            "recycles": counts.get("recycles", 0),
            "prompts_attempted": counts.get("prompts_attempted", 0),
            "elapsed_s": round(float(elapsed), 2),
            "note": note or "",
        }
        (trace_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    except Exception:
        # Trace capture must never break the run.
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Research kernel.")
    ap.add_argument("--config", required=True, type=Path,
                    help="Variant TOML (see config_base.toml for schema)")
    ap.add_argument("--results-tsv", required=True, type=Path)
    ap.add_argument("--exp-id", default=None,
                    help="Experiment id (default: exp_<unix-ts>)")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    exp_id = args.exp_id or f"exp_{int(time.time())}"
    metric, stats = run_kernel(args.config, args.results_tsv, exp_id,
                               args.note)
    print(f"exp_id={exp_id} metric={metric:.3f} "
          f"done={stats['done']} skip={stats['skipped']} "
          f"timeout={stats['timed_out']} elapsed={stats['elapsed_s']:.1f}s "
          f"sha={stats['config_sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
