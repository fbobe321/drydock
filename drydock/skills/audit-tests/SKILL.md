---
name: audit-tests
description: Audit tests for weakness. Runs audit_sampler to flag tests that pass on broken code (keyword too short, argparse-error passes, pwd-state leakage). Upgrades weak tests on request.
allowed-tools: bash read_file grep search_replace write_file
user-invocable: true
---

# Audit Tests for Weakness

Weak tests create fake confidence. Before accepting a "green" build,
audit the tests themselves. This skill drives `scripts/audit_sampler.py`
and the `/strong-tests` skill to close the loop.

## When to run

- After writing or modifying `functional_tests.sh`.
- After drydock iteration claims X/Y tests pass — verify those passes
  are real.
- When a PRD is reported as "clean" but the behavior doesn't feel right.

## Run the audit

```bash
python3 /data3/drydock/scripts/audit_sampler.py --dir . --sample-rate 1.0
```

(`--sample-rate 1.0` = audit every test, not just 5% — for a single PRD
the cost is tiny and we want complete coverage.)

The audit checks each passing test for these failure modes:

| Flag | Meaning |
| --- | --- |
| `keyword ≤ 2 chars` | Single-char/digit keyword matches almost any output |
| `keyword in --help` | Keyword also appears in --help, so a broken command that prints --help still "passes" |
| `argparse error = fake pass` | Output is just `usage: ... error: ...` — the command crashed but behavioral test doesn't catch it |
| `output looks like --help` | Same issue via a different path |
| `output differs between runs` | Non-determinism (may be legit for RNG tools) |

## What to do with flags

For each suspicious test:

1. **Is the code actually right?** Run the command yourself and read
   the output. If the output is wrong, fix the SOURCE, not the test.
2. **Is the test too permissive?** Upgrade it:
   - Replace `grep -q "prime"` with `[ "$OUT" = "17 is prime" ]`.
   - Replace `[ -n "$OUT" ]` with a specific property check.
   - Add a hermetic fixture dir if the test depends on `pwd` state.
3. **Does the test need to be replaced entirely?** If the feature can
   be tested via roundtrip or state-sequence, replace the keyword test
   with that approach (see `/strong-tests` skill).

## When the audit reports 0 suspects

That's good but not sufficient. The audit sampler only catches MECHANICAL
weaknesses (keyword-in-help, argparse-error). It doesn't verify that the
test's EXPECTED VALUE is semantically right. Run `/strong-tests` too for
deeper coverage.

## Integration with iteration loops

The meta_ralph / mega_loop flows should run an audit after each
improvement cycle:

```bash
# After drydock claims pass rate improved:
python3 /data3/drydock/scripts/audit_sampler.py --dir . --sample-rate 1.0
# If any flags: upgrade those tests, re-run drydock iteration, re-audit.
```

This prevents drydock from gaming weak tests: as the tests strengthen,
only real fixes survive.
