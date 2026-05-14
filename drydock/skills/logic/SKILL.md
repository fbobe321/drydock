---
name: logic
description: "Propositional / first-order logic reference — symbols, truth tables, rules of inference, equivalence laws, proof methods, quantifiers. Use BEFORE asserting any if/then/iff/contrapositive/De-Morgan-shaped claim or before reaching for a formal proof. Source — Harold's Logic Cheat Sheet (Toomey, 2026)."
allowed-tools: read_file math logic
user-invocable: true
---

# Logic — quick reference

Invoke when you need to: rewrite an implication via contrapositive, apply
De Morgan, name a fallacy, structure a proof, or check whether two
propositional expressions are equivalent. The full 832-line cheat sheet
lives at `assets/cheat_sheet_full.txt` — read that for the corners this
SKILL.md doesn't cover (set theory, Boolean algebra, propositional /
first-order proof scaffolding).

## 1. Symbols

| Op | Symbols | Example | Reads as |
|---|---|---|---|
| AND (conjunction) | `∧` | `p ∧ q` | p and q |
| OR (disjunction) | `∨` | `p ∨ q` | p or q (inclusive) |
| NOT (negation) | `¬`, `~` | `¬p` | not p |
| → (conditional) | `→`, `⇒`, `⊃` | `p → q` | if p then q; p implies q; p only if q |
| ↔ (biconditional) | `↔`, `⇔` | `p ↔ q` | p iff q; p is necessary and sufficient for q |
| ∀ (universal) | `∀x` | `∀x P(x)` | for all x, P(x) |
| ∃ (existential) | `∃x` | `∃x P(x)` | there exists an x such that P(x) |
| ≡ (equivalence) | `≡` | `e1 ≡ e2` | always same truth value |

## 2. Core truth tables

```
p q | p∧q | p∨q | p→q | p↔q | p⊕q | ¬p
F F |  F  |  F  |  T  |  T  |  F  | T
F T |  F  |  T  |  T  |  F  |  T  | T
T F |  F  |  T  |  F  |  F  |  T  | F
T T |  T  |  T  |  T  |  T  |  F  | F
```

`p → q` is FALSE only when `p=T, q=F`. Everything else is true (vacuously
true when `p=F`).

## 3. Implication shape (memorize)

| Form | Symbol | Equivalent? |
|---|---|---|
| **Contrapositive** of `p → q` | `¬q → ¬p` | **YES** (equivalent — always safe to flip) |
| Converse of `p → q` | `q → p` | NO — different statement |
| Inverse of `p → q` | `¬p → ¬q` | NO — different statement |

Affirming-the-consequent (`p→q, q ⊢ p`) and denying-the-antecedent
(`p→q, ¬p ⊢ ¬q`) are **formal fallacies**. Reject them.

## 4. Rules of inference (proposition-level)

| # | Name | Pattern | English |
|---|---|---|---|
| 1 | Modus Ponens | `p; p→q ⊢ q` | If p, then q. p. Therefore q. |
| 2 | Modus Tollens | `p→q; ¬q ⊢ ¬p` | If p then q. Not q. Therefore not p. |
| 3 | Hypothetical Syllogism | `p→q; q→r ⊢ p→r` | Transitivity of implication |
| 4 | Disjunctive Syllogism | `p∨q; ¬p ⊢ q` | Elimination |
| 5 | Constructive Dilemma | `p∨q; (p→r)∧(q→s) ⊢ r∨s` | |
| 6 | Simplification | `p∧q ⊢ p` | Take a conjunct |
| 7 | Conjunction | `p; q ⊢ p∧q` | Combine premises |
| 8 | Addition | `p ⊢ p∨q` | Weaken to a disjunction |
| 9 | Resolution | `p∨q; ¬p∨r ⊢ q∨r` | |
| 10 | Proof by Cases | `p∨q; p→r; q→r ⊢ r` | |
| 11 | Contradiction Rule | `¬p → F ⊢ p` | Reductio |

## 5. Equivalence laws (rewrite rules)

```
Identity        p ∨ F ≡ p              p ∧ T ≡ p
Domination      p ∨ T ≡ T              p ∧ F ≡ F
Idempotent      p ∨ p ≡ p              p ∧ p ≡ p
Double Negation ¬¬p ≡ p
Complement      p ∨ ¬p ≡ T             p ∧ ¬p ≡ F
Commutative     p ∨ q ≡ q ∨ p          p ∧ q ≡ q ∧ p
Associative     (p∨q)∨r ≡ p∨(q∨r)      (p∧q)∧r ≡ p∧(q∧r)
Distributive    p∧(q∨r) ≡ (p∧q)∨(p∧r)  p∨(q∧r) ≡ (p∨q)∧(p∨r)
Absorption      p ∨ (p ∧ q) ≡ p        p ∧ (p ∨ q) ≡ p
De Morgan       ¬(p∨q) ≡ ¬p ∧ ¬q       ¬(p∧q) ≡ ¬p ∨ ¬q
Implication     p → q ≡ ¬p ∨ q         ¬(p → q) ≡ p ∧ ¬q
Biconditional   p ↔ q ≡ (p→q) ∧ (q→p)
```

De Morgan + Implication rewrites are the most common ones in proofs and
in code review ("you said `not (A and B)`; that's `(not A) or (not B)`").

## 6. Proof methods

| Method | Sketch |
|---|---|
| **Direct** | Assume `p`, derive `q`, conclude `p → q`. |
| **Contrapositive** | To show `p → q`, show `¬q → ¬p`. Often easier when `q` is negated. |
| **Contradiction (Indirect)** | Assume `¬p`, derive a contradiction (e.g. `r ∧ ¬r`), conclude `p`. |
| **By cases / exhaustion** | If `p₁ ∨ p₂ ∨ ... ∨ pₙ` and each `pᵢ → q`, then `q`. |
| **Induction** | Prove base case + (P(k) → P(k+1)). |
| **Construction (example)** | For ∃-claims, exhibit a witness. |
| **Counterexample** | For ∀-claims, exhibit one violation to disprove. |

Contradiction and contrapositive are dual: a proof by contrapositive IS
a special-case proof by contradiction.

## 7. Quantifiers

- `∀x P(x)` — "for all x, P(x)". Disproved by **one** counterexample.
- `∃x P(x)` — "there exists x such that P(x)". Proved by **one** witness.

**Negation of quantifiers** (move the `¬` inside, flip the quantifier):
- `¬∀x P(x) ≡ ∃x ¬P(x)`
- `¬∃x P(x) ≡ ∀x ¬P(x)`

**Order matters when mixed:**
- `∀x ∃y P(x, y)` — "every x has some y" (y can depend on x)
- `∃y ∀x P(x, y)` — "some y works for every x" (one y, all xs)

The second is strictly stronger.

## 8. When to invoke this skill

The harness should pull `logic` when the user message contains any of:

- "prove", "show that", "iff", "if and only if"
- "contrapositive", "converse", "inverse"
- "De Morgan", "tautology", "contradiction"
- "for all", "there exists", "∀", "∃"
- "Modus Ponens", "Modus Tollens"
- "necessary and sufficient", "necessary condition", "sufficient condition"

The model should call `read_file` on `assets/cheat_sheet_full.txt` for
sections this summary doesn't cover (Boolean algebra ID, set theory
laws, predicate-logic proof scaffolding, deeper quantifier rules).

## Source

Toomey, Harold. *Harold's Logic Cheat Sheet*. 5 May 2026.
<https://www.toomey.org/tutor/harolds_cheat_sheets/Harolds_Logic_Cheat_Sheet.pdf>
Mirrored locally at `assets/cheat_sheet_full.txt` in this skill.
