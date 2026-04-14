---
name: strong-tests
description: Write STRONG functional tests for the current package. Exact match, roundtrip, state, hermetic. Replaces or augments weak auto-generated tests.
allowed-tools: bash read_file grep search_replace write_file
user-invocable: true
---

# Writing Strong Functional Tests

The auto-generated `functional_tests.sh` is a weak floor (keyword grep,
runs-without-crash). **Weak tests pass even when the code is broken.**
This skill writes a `strong_tests.sh` that actually verifies the PRD's
contract.

Invoke as `/strong-tests` in the TUI after building a package, or ask
for it explicitly ("write strong tests for X").

## What STRONG means

1. **Exact string match**, not keyword grep.
   - Weak: `echo "$OUT" | grep -q "14"` — passes for `14.0` too.
   - Strong: `[ "$OUT" = "14" ]` — exact.

2. **Roundtrip properties** for every invertible operation:
   - `decrypt(encrypt(x, k), k) == x`
   - `parse(serialize(x)) == x`
   - `decode(encode(x)) == x`
   - `inverse(f(inverse(f(x)))) == x`

3. **State sequences**: for stateful tools, test THROUGH the workflow.
   - `init → add → commit → log` must show the commit message.
   - `create X; list` must contain X.
   - `create X; delete X; list` must NOT contain X.

4. **Hermetic** — fresh tmp dir, `rm -rf` first, no pwd state:
   ```bash
   TDIR=/tmp/<pkg>_strong_$$
   mkdir -p "$TDIR"
   cd "$TDIR"
   export PYTHONPATH="/path/to/pkg_parent:$PYTHONPATH"
   # ... tests ...
   rm -rf "$TDIR"
   ```

5. **Error cases tested explicitly**: division by zero, bad input, missing
   file, out-of-range value. Each must produce SPECIFIC error text or
   non-zero exit code.

6. **Edge cases** from the domain:
   - Numbers: 0, 1, -1, MAX_INT, negative, float-vs-int
   - Strings: empty "", single char, unicode, whitespace-only
   - Collections: empty, single item, many items, duplicates

## Workflow

1. Read `PRD.md` or `PRD.master.md`. List every feature + expected
   behavior.
2. For each feature, plan a specific input → expected output pair.
3. For every invertible operation, add a roundtrip test.
4. For every stateful operation, add a multi-step sequence test.
5. Write `strong_tests.sh` with `set +e`, PASS/FAIL counters, hermetic
   fixture dir, exact-match assertions via a helper:
   ```bash
   assert_eq() {
       if [ "$2" = "$3" ]; then pass "$1"
       else fail "$1: expected [$3] got [$2]"; fi
   }
   ```
6. Run it. Each FAIL is a real bug — fix the source, not the test.
7. After all pass, run `functional_tests.sh` too and ensure it
   also passes (the strong tests subsume the weak).

## Anti-patterns (refuse to write these)

- `grep -q` of single word — weak.
- `[ -n "$OUT" ]` as a feature test — only a floor check.
- Tests that depend on `pwd` state from previous runs.
- Tests that pass when the package doesn't exist (check output for
  "No module named" and fail).
- Tests that don't assert a specific result — only "didn't crash".

## Example invocation

User: "write strong tests for calculator"

1. Read PRD: calculator evaluates expressions, supports variables.
2. Plan inputs:
   - `"2+3*4"` → exactly `"14"` (precedence)
   - `"10-4-2"` → exactly `"4"` (left-associativity)
   - `"x=5" "x*2"` → `"10"` (variable)
   - `"10/0"` → must error
   - `"sqrt(144)"` → `"12"` or `"12.0"`
3. Write `strong_tests.sh` with exact-match assertions.
4. Run. Fix source for any failures.
