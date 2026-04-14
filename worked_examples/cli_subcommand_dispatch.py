"""Worked example: canonical CLI with argparse subcommands.

Addresses MODEL_SHORTCOMINGS #2 (scaffolding without wiring) — the
minivc anti-pattern where the model wrote:

    from minivc import init, status, add
    ...
    if args.command == "init":
        init.run()           # AttributeError — init is a FUNCTION
    elif args.command == "status":
        status.run()

and --help worked (argparse never dispatched) but every real
subcommand crashed. Gemma 4 doesn't trace the call graph end-to-end
and never noticed `init` was imported as a callable, not a class.

The critical lesson:

  1. Decide ONE shape. Either subcommand modules export a class
     with a `run()` method, OR they export a callable function.
     DON'T mix them.

  2. The dispatch in cli.py must match that shape exactly.

  3. After writing the dispatch, run `python3 -m pkg <each_cmd>` to
     confirm each one actually executes.

The pattern below uses plain CALLABLE FUNCTIONS — the simpler choice.
Each subcommand is a function that takes `args` and runs. The cli
dispatch is a dict mapping command-name → function.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ─── Subcommand implementations (each in its own file normally) ──────

def cmd_init(args: argparse.Namespace) -> int:
    """`pkg init [--name NAME]` — initialize a new workspace."""
    name = args.name or "unnamed"
    workspace = Path(f".{name}_workspace")
    workspace.mkdir(exist_ok=True)
    print(f"Initialized: {workspace}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """`pkg status` — show workspace state."""
    found = sorted(Path.cwd().glob(".*_workspace"))
    if not found:
        print("No workspace — run `init` first.")
        return 1
    for p in found:
        print(f"  {p.name}: {len(list(p.iterdir()))} items")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """`pkg add PATH` — add a file to the workspace."""
    target = Path(args.path)
    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        return 1
    # Find the first workspace
    ws = next(iter(sorted(Path.cwd().glob(".*_workspace"))), None)
    if ws is None:
        print("Error: no workspace — run `init` first", file=sys.stderr)
        return 1
    dest = ws / target.name
    dest.write_bytes(target.read_bytes())
    print(f"Added: {dest}")
    return 0


# ─── The dispatcher ──────────────────────────────────────────────────

# Map subcommand name → the callable that implements it. THIS is how
# argparse ties argv[1] ("init"/"status"/"add") to the code that runs.
# One source of truth; no chance of `init.run()` / `init()` mismatch.
COMMANDS = {
    "init":   cmd_init,
    "status": cmd_status,
    "add":    cmd_add,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkg")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="initialize a workspace")
    p_init.add_argument("--name", default=None)

    p_status = sub.add_parser("status", help="show workspace state")  # noqa: F841

    p_add = sub.add_parser("add", help="add a file")
    p_add.add_argument("path")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Dispatch via the dict — argparse gave us args.command, which MUST
    # be a key in COMMANDS because we set required=True on subparsers.
    fn = COMMANDS[args.command]
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
