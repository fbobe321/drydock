---
name: fiar-cap
description: Draft a Corrective Action Plan for the open FIAR findings (NFRs)
---
Draft Corrective Action Plans for the open findings in the FIAR engagement at: $ARGS

1. Call FiarControls (path only) to see the engagement, then read the findings by
   listing deficient controls (FiarControls status=deficient) and, for each, its
   recorded NFR. If there are no findings, say so and STOP.
2. Take ONE finding at a time (worst severity first: material_weakness, then
   significant_deficiency, then deficiency). For it, draft a CAP that is specific
   and testable:
   - root_cause: the underlying reason the control failed (people / process /
     system / policy) — go past the symptom in the NFR condition;
   - action: the concrete corrective action that will make the control operate;
   - responsible: the organization / POC who owns it;
   - target_date: a realistic milestone date;
   - milestones: 2–4 dated, verifiable steps (e.g. "update SOP", "train preparers",
     "re-test 25 samples with 0 exceptions").
3. The CAP must close the specific assertion gap the NFR identified — tie the final
   milestone to a re-test of the same control with zero exceptions. Note whether the
   fix is a process change, a system change, or an ITGC (access / SoD / change
   management) remediation, since those route to different owners.

Present each CAP in the standard structure (finding → root cause → action →
responsible → milestones), and end with which finding to remediate first and why.
