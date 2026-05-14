---
name: prove
description: "Workflow for picking and executing a proof method on a math claim. Walks you through method selection (direct / contrapositive / contradiction / cases / induction / example / counterexample) and shows where each symbolic-math tool (logic, algebra, number_theory, set, linear_algebra) plugs in. Use when a user message contains prove / show that / demonstrate / verify."
allowed-tools: logic algebra number_theory set linear_algebra math read_file
user-invocable: true
---

# How to prove a claim

This skill is a **decision tree + checklist**, not a reference (the
`logic` skill is the reference for symbols, rules, and equivalences).
Invoke when the user says "prove", "show that", "demonstrate",
"verify", "iff", "necessary and sufficient", or "for all / there
exists".

## Step 1 — Identify the claim shape

Read the claim carefully. Classify into one of these shapes:

| Shape | Looks like | Default method |
|-------|-----------|----------------|
| Conditional | `If P, then Q` / `P → Q` / `P implies Q` | direct → contrapositive → contradiction |
| Biconditional | `P iff Q` / `P ↔ Q` / `P is necessary and sufficient for Q` | prove both directions separately |
| Universal | `For all x, P(x)` / `∀x P(x)` | direct on arbitrary x → induction → contradiction |
| Existential | `There exists x such that P(x)` / `∃x P(x)` | construction (exhibit a witness) |
| Negation | `Not P` / `¬P` | reduce to P → False via contradiction |
| Equivalence | `A ≡ B` (logical or algebraic) | rewrite A to B (or both to a common form) |
| Counterexample | `For all x, P(x)` is FALSE | construction (exhibit one violating x) |

If you can't classify in 30 seconds, the claim is malformed — ask the
user for clarification.

## Step 2 — Pick the cheapest method first

Don't reach for induction or proof-by-contradiction when a direct
calculation will work. Order to try:

1. **Direct symbolic check.** If both sides are computable, just compute.
   - `algebra(op="equivalent_check"` style)
   - Equality of expressions: `algebra(op="simplify", expression="<lhs> - <rhs>")` → if 0, equal.
   - For two boolean expressions: `logic(op="equivalent", expression="<A>", expression2="<B>")`
2. **Direct case analysis.** If the claim is `∀x P(x)` over a small
   finite domain (say |D| ≤ 6), enumerate.
   - `logic(op="truth_table", expression="<expr>")` for boolean.
   - `algebra(op="evaluate", expression="<P(x)>", variable="x", value="<a>")` for each `a`.
3. **Use the contrapositive.** If `P → Q` has `Q` involving a negation
   (e.g. "if n²is odd then n is odd"), prove `¬Q → ¬P` instead. Use
   `logic(op="contrapositive", expression="<P> >> <Q>")` to derive the
   target, then prove THAT directly.
4. **Proof by contradiction.** Assume `¬<claim>`, derive a contradiction.
   `logic(op="contradiction", expression="<assumption>")` can check whether
   a candidate assumption is *self*-contradictory.
5. **Induction.** Use only when the claim is parameterized by a natural
   number: base + (P(k) → P(k+1)).
6. **Construction.** For existentials and counterexamples, **find one
   example**. Don't argue abstractly that one exists.

## Step 3 — Execute and verify each step

Run the chosen method **with the tools, not in your head**.

### Direct symbolic equivalence

```
# Claim: (x-1)(x+1) = x² - 1
algebra(op="simplify", expression="(x-1)*(x+1) - (x**2 - 1)")
# → 0   ⇒  proved
```

### Boolean equivalence

```
# Claim: p → q ≡ ¬p ∨ q
logic(op="equivalent", expression="p >> q", expression2="~p | q")
# → true (Implies(p, q) ≡ q | ~p)
```

### Contrapositive

```
# Claim: if n² is odd then n is odd
# Contrapositive: if n is even then n² is even (easier)
logic(op="contrapositive", expression="(n % 2 == 1) >> (n % 2 == 1)")
# ...then prove the resulting (¬Q → ¬P) directly.
```

### Counterexample for "for all"

```
# Claim: ∀n, n² > n
# This is FALSE for n=0 and n=1. Just produce one:
algebra(op="evaluate", expression="n**2 > n", variable="n", value="0")
# → False  ⇒  counterexample found
```

### Existence by construction

```
# Claim: ∃ integers a, b with a² + b² = 5
# Witness: a=1, b=2.  Verify:
math(expression="1**2 + 2**2")  # → 5  ⇒  proved
```

### Number-theoretic claim

```
# Claim: 2^32 - 1 is composite
number_theory(op="is_prime", n="2**32 - 1")  # → False
number_theory(op="factor", n="2**32 - 1")    # → exhibits factorization
```

### Matrix-algebra claim

```
# Claim: A = [[2,1],[0,3]] has eigenvalues {2, 3}
linear_algebra(op="eigenvals", matrix="2, 1; 0, 3")
# → {2: 1, 3: 1}  ⇒  proved
```

## Step 4 — Write the final answer

After tool verification, write the proof out:

1. State the method (Direct / Contrapositive / Contradiction / etc.).
2. Show the key algebraic / logical step (with the tool result).
3. Conclude with the original claim.
4. End with `FINAL ANSWER: <claim is true / claim is false (counterexample: ...) / claim is X>` so the
   judge can extract.

## Anti-patterns to refuse

- **Don't** prove in plain English without the tools when the math is
  computable. The model gets sign errors, drops terms, and miscounts
  cases. sympy doesn't.
- **Don't** assert "it's obvious that X" when X is the thing being asked.
- **Don't** start with induction unless the claim is over ℕ. Most HLE
  Math claims have closed-form or finite-case proofs that are cheaper.
- **Don't** prove the converse and call it a day — `P → Q` is NOT the
  same as `Q → P`. If the user asked for `P → Q`, prove that direction.

## Source

This workflow combines:
- The seven proof methods from Toomey's *Logic Cheat Sheet* (see the
  `logic` skill for the cheat sheet text).
- The 67 symbolic-math operations across `logic`, `algebra`,
  `number_theory`, `set`, and `linear_algebra` built-ins shipped
  2026-05-14 as a direct offload path for small-model symbolic work.
