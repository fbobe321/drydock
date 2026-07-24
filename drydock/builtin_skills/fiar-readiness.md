---
name: fiar-readiness
description: Assess audit-readiness posture of a FIAR engagement and report gaps
---
Produce a Discover-phase audit-readiness assessment for the FIAR engagement at: $ARGS

1. Call FiarControls (path only) to see the whole matrix: cycle, Wave, phase, and
   the status counts. If the engagement JSON does not exist yet, tell the operator
   to create one first: `python -m drydock.fiar new <path> <name> <cycle>` (cycles:
   FBWT, P2P, PPE, INV, CIVPAY, REIM, FR, ITGC, or * for all).
2. Call FiarControls with status=deficient and status=not_tested to enumerate the
   gaps. For each DEFICIENT control, note the assertion(s) at risk and whether an
   NFR already exists for it.
3. Read the current readiness roll-up (the counts show tested vs effective). State,
   in plain terms:
   - percent of applicable controls tested, and of those, percent effective;
   - the assertions (Existence, Completeness, Valuation, Presentation & Disclosure,
     Rights & Obligations) that are NOT yet supported because a control covering
     them is deficient or untested;
   - open findings by severity (material weakness / significant deficiency /
     deficiency) and whether each has a Corrective Action Plan.
4. Recommend the highest-value next actions to move toward assertion, prioritizing
   material weaknesses and untested key controls (FBWT reconciliation, three-way
   match, access/SoD ITGCs). Do NOT claim the entity is audit-ready — readiness is
   asserted by management and validated by an independent examination; your job is
   to report the evidenced posture and the gaps.

Report a short readiness summary (3–4 sentences) first, then the detail beneath it.
