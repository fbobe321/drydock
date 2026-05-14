---
name: constraint-reasoning
description: "How to encode a problem as constraints and solve it with the `solve` tool (Z3 SMT). Use when the question has the shape 'find values such that ...', a list of conditions to satisfy simultaneously, optimization under constraints, or a logic puzzle. Strictly more reliable than reasoning step-by-step on these problems."
allowed-tools: solve logic algebra number_theory math read_file
user-invocable: true
---

# Constraint reasoning — use `solve` instead of brain-arithmetic

When a problem looks like "find x such that A, B, C all hold," or
"prove this conclusion follows from these premises," or "what's the
largest x with property P," **don't compute by hand**. Translate the
constraints into the `solve` tool and let Z3 do the search.

Z3 is an industrial SMT solver. It handles integer/real arithmetic,
modular arithmetic, boolean logic, bitvectors, optimization, and
enumeration. It is **complete and sound** for the fragments it
supports — when it says `sat`, the model is real; when it says `unsat`,
there is provably no solution.

## When to invoke this skill

Any of these phrases in the user message:

- "find x such that", "find all values of", "for what value of"
- "exists / there is a", "what's the smallest / largest"
- "if and only if", "necessary and sufficient" (use `prove`)
- explicit list of conditions (3+ conditions on one variable)
- "Einstein puzzle", "logic puzzle", "Sudoku", "N-queens"
- "constraint", "subject to", "such that"
- "modular arithmetic", "mod N", "≡ ... (mod ...)"

If you see the question type **AND you'd otherwise have to enumerate**,
that's a strong signal — call `solve`.

## Five worked examples (memorize the encoding pattern)

### 1. Linear system

> "Find x, y such that x + y = 10 and x − y = 4."

```
solve(
  op="solve",
  variables="x:Int, y:Int",
  constraints=["x + y == 10", "x - y == 4"],
)
→ sat: x=7, y=3
```

### 2. Modular arithmetic

> "Find the smallest non-negative x such that 3x ≡ 5 (mod 7)."

```
solve(
  op="optimize",
  variables="x:Int",
  constraints=["x >= 0", "x < 7", "3*x % 7 == 5"],
  objective="x",
  direction="min",
)
→ optimal: x=4
```

Use `%` for modulo (Python style). Always bound `x` to the range you
care about, or Z3 will return an arbitrary representative.

### 3. Einstein-style logic puzzle

> "Three nationalities live in three different houses (1, 2, 3).
> The Norwegian lives in house 1. The Brit lives next to the
> Norwegian. The German doesn't live in house 2. Who lives where?"

```
solve(
  op="solve",
  variables="norw:Int, brit:Int, ger:Int",
  constraints=[
    "norw >= 1", "norw <= 3",
    "brit >= 1", "brit <= 3",
    "ger >= 1", "ger <= 3",
    "Distinct(norw, brit, ger)",
    "norw == 1",
    "Abs(brit - norw) == 1",
    "ger != 2",
  ],
)
→ sat: norw=1, brit=2, ger=3
```

`Distinct(...)` is the all-different constraint. `Abs(x)` is built in.

### 4. Optimization under constraints

> "Maximize x·y subject to x + y = 10, x ≥ 0, y ≥ 0."

```
solve(
  op="optimize",
  variables="x:Int, y:Int",
  constraints=["x + y == 10", "x >= 0", "y >= 0"],
  objective="x * y",
  direction="max",
)
→ optimal: x=5, y=5, objective=25
```

### 5. Counterexample / disproof

> "Prove that for all positive integers x, x² > x."

```
solve(
  op="prove",
  variables="x:Int",
  constraints=["x > 0"],
  conclusion="x * x > x",
)
→ countered: x=1   (because 1² = 1, NOT > 1)
```

When `prove` returns `countered`, the model field is the
counterexample. Use it as the answer.

## Variable types — pick the right one

| Type | Use for | Example |
|------|---------|---------|
| `Int` | counts, integers, integer parameters | `x:Int` |
| `Real` | continuous quantities, real arithmetic | `pi:Real` |
| `Bool` | propositions, on/off, yes/no | `is_prime:Bool` |
| `BitVec<N>` | fixed-width integers (with overflow), bitmasks | `byte:BitVec8` |

**Pitfall:** `Real` constraints over `Int` variables silently coerce.
If you need integer answers, declare `Int`.

## Operations — pick the right one

| op | Use when | Returns |
|----|----------|---------|
| `solve` | "find any solution" | one assignment or `unsat` |
| `find_all` | "find all / enumerate up to N" | list of distinct assignments |
| `prove` | "show / verify / does X follow from Y" | `valid` or counterexample |
| `optimize` | "find min/max", "smallest / largest" | optimal assignment + value |

## Common encoding patterns

- **All-different**: `Distinct(a, b, c, ...)` — used in Sudoku, puzzles
- **At least one**: `Or(a, b, c, ...)` (with Bool vars)
- **Exactly one of K**: `Sum([If(b1, 1, 0), If(b2, 1, 0), ...]) == 1`
- **Conditional**: `If(cond, then_expr, else_expr)`
- **Implication**: `Implies(p, q)`
- **Range**: `And(x >= lo, x <= hi)` or two separate constraints
- **Divisibility (k | n)**: `n % k == 0`
- **Bounded sum**: `Sum([x1, x2, x3]) == total`

## Pitfalls and limits

- **Real arithmetic with transcendentals** (sin, log, e) is not Z3's
  strength — for those, use the `math` tool or `algebra` tool instead.
- **Unbounded quantification** (`∀x ∃y ...`) is decidable in narrow
  fragments only. Encode quantifier-free when possible.
- **Sandbox**: constraint strings are AST-validated. No attribute
  access, no calls outside the Z3 whitelist (And/Or/Not/Implies/If/
  Distinct/Abs/Sum/Xor).
- **Default timeout** is 5s. Bump via `timeout_ms` for harder
  problems, up to 30s.
- **Variable cap**: 64 declared variables max. For larger problems
  (Sudoku, big puzzles), encode them as a single `BitVec` or use
  `Sum` over an If-list rather than 81 named cells.

## How this skill plugs in with the others

| Skill / Tool | When over `solve` |
|--------------|-------------------|
| `logic` (skill) | Reference for propositional rules — read FIRST if you're unsure of contrapositive / De Morgan / etc. |
| `prove` (skill) | Workflow for picking a proof method. `solve(op="prove")` is one option it routes to. |
| `algebra` (tool) | Symbolic manipulation (factor, expand, simplify) — for equations that aren't constraints. |
| `number_theory` (tool) | Primes, gcd, factorization — closed-form questions that don't need a search. |
| `math` (tool) | Plain arithmetic — `solve` is overkill for `23 * 47`. |

The general rule: **if the problem is "what value satisfies these
conditions," reach for `solve`. If it's "what is f(x) for a given x,"
reach for `math` / `algebra`.**

## Output shape recap

```
SolveResult {
  ok: True
  status: "sat" | "unsat" | "unknown" | "valid" | "countered"
        | "optimal" | "infeasible"
  model: "x=7, y=3"     # one-line assignment
  models: [...]         # only for find_all
  objective_value: "25" # only for optimize
}
```

Read `status` first. `sat`/`valid`/`optimal` means success. `unsat`
means the constraints contradict each other. `countered` means the
conclusion is false and `model` shows the counterexample.
