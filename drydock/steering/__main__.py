"""Deep Noir steering CLI — `python -m drydock.steering <subcommand>`.

Subcommands:

    list                       Show all discovered modes + vector counts.
    inspect <name>             Show one vector's full manifest.
    sha256 <path>              Helper for vector authors: compute sha256
                               to put in a manifest after dropping in
                               a new .npy payload.
    eval <mode> --prompts <f>  Run a stub sandbox eval for the named
                               mode. v0 wires a `LogOnlySteeringApplier`
                               so this completes without actual model
                               inference — useful for verifying that
                               vectors load + the sandbox plumbing
                               works end-to-end before vLLM-side
                               integration lands.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from drydock.steering.applier import (
    LogOnlySteeringApplier,
    SteeringDecision,
)
from drydock.steering.config import SteeringConfig
from drydock.steering.registry import load_registry
from drydock.steering.sandbox import run_sandbox
from drydock.steering.vectors import compute_sha256


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drydock.steering",
        description="Deep Noir activation-steering scaffolding.",
    )
    p.add_argument(
        "--root",
        default=None,
        help="Vectors root (default: ~/.drydock/steering/vectors)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List discoverable modes + vector counts")

    sp_inspect = sub.add_parser(
        "inspect", help="Show one vector's manifest"
    )
    sp_inspect.add_argument("name")

    sp_sha = sub.add_parser(
        "sha256",
        help="Compute sha256 of a payload (for manifest authoring)",
    )
    sp_sha.add_argument("path")

    sp_eval = sub.add_parser(
        "eval", help="Run the sandbox eval (stub completion in v0)"
    )
    sp_eval.add_argument("mode")
    sp_eval.add_argument("--prompts", required=True,
                         help="Path to a file with one prompt per line")
    sp_eval.add_argument("--active-model", default="gemma4")
    sp_eval.add_argument("--scale", type=float, default=None,
                         help="Override the manifest's default scale")
    sp_eval.add_argument("--bad-pattern", action="append", default=[],
                         help="String that should NOT appear in output "
                              "(may be passed multiple times)")
    sp_eval.add_argument("--out", default=None,
                         help="Write structured result JSON to this path")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    registry = load_registry(args.root)

    if args.cmd == "list":
        modes = registry.list_modes()
        if not modes:
            print(f"No vectors discovered under {registry.root}.")
            print("Drop manifests into <root>/<mode>/<name>.toml + <name>.npy.")
            return 0
        for mode in modes:
            vectors = registry.vectors_for_mode(mode)
            print(f"{mode}: {len(vectors)} vector(s)")
            for v in vectors:
                print(f"  - {v.name} (layer {v.layer}, scale {v.scale}, "
                      f"target {v.target_model})")
        return 0

    if args.cmd == "inspect":
        m = registry.find_by_name(args.name)
        if m is None:
            print(f"vector not found: {args.name!r}", file=sys.stderr)
            return 1
        print(json.dumps(asdict(m), indent=2, default=list))
        return 0

    if args.cmd == "sha256":
        path = Path(args.path)
        if not path.is_file():
            print(f"file not found: {path}", file=sys.stderr)
            return 1
        print(compute_sha256(path))
        return 0

    if args.cmd == "eval":
        prompts_path = Path(args.prompts)
        if not prompts_path.is_file():
            print(f"prompts file not found: {prompts_path}", file=sys.stderr)
            return 1
        prompts = [
            line.strip() for line in prompts_path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]

        scales = {args.mode: args.scale} if args.scale is not None else {}
        config = SteeringConfig.from_mode_names([args.mode], scales=scales)

        # v0 stub: completion is a placeholder. Real deployments inject
        # the harness's chat-completion. This still verifies that vectors
        # load, integrity check passes, modes resolve, and the sandbox
        # plumbing produces a usable summary.
        def stub_completion(prompt: str, decision: SteeringDecision) -> str:
            return f"[stub: {len(decision.applied_vectors)} vectors] {prompt}"

        summary = run_sandbox(
            config=config,
            prompts=prompts,
            registry=registry,
            applier=LogOnlySteeringApplier(),
            completion_fn=stub_completion,
            active_model=args.active_model,
            bad_patterns=tuple(args.bad_pattern),
        )

        print(f"Sandbox eval — mode {args.mode!r}, model {args.active_model!r}")
        print(f"  prompts:           {len(summary.per_prompt)}")
        print(f"  regressions:       {summary.regressions}")
        print(f"  fixes:             {summary.fixes}")
        print(f"  unchanged outputs: {summary.unchanged_outputs}")
        print(f"  distinct outputs:  {summary.distinct_outputs}")
        print(f"  passed:            {summary.passed()}")

        if args.out:
            summary.write_json(args.out)
            print(f"  wrote: {args.out}")

        return 0 if summary.passed() else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
