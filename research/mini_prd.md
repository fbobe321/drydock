# PRD: mini_cli

A tiny calculator CLI. One file. Four functions. Built from scratch by
drydock in a single bootstrap turn so the research kernel spends most of
its 5-minute budget on stimulus prompts, not package construction.

## File

`mini_cli.py` — single file, no package directory.

## Operations

Four binary operations exposed via argparse subcommands:

- `mini_cli add a b` → prints `a + b`
- `mini_cli sub a b` → prints `a - b`
- `mini_cli mul a b` → prints `a * b`
- `mini_cli div a b` → prints `a / b`, exits 1 with an error message on division by zero

## Interface

Invocation: `python3 mini_cli.py <op> <a> <b>`

Arguments parsed as floats; output printed as plain number (no extra text).
Numeric output trims trailing `.0` for integer results so `add 2 3` prints
`5`, not `5.0`.

## Non-requirements

No tests, no docstrings beyond a one-line module header, no config file,
no logging. The point is a minimal build target, not a polished package.
