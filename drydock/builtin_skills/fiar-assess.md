---
name: fiar-assess
description: Test the next un-tested FIAR control objective against real evidence
---
Test FIAR control objectives in the engagement at: $ARGS

Do exactly ONE control per run (loop it — /loop N /fiar-assess <path> — for the
whole matrix):

1. Call FiarControls with that path and status=not_tested. If none remain, say the
   control matrix is fully tested and STOP without calling other tools.
2. Take the FIRST not_tested control. Call FiarControl (path + id) to read its
   control objective, assertions, required Key Supporting Documents (KSDs), and
   test procedure.
3. You MUST examine real evidence before deciding — do NOT guess. Follow the test
   procedure: Read the actual KSD the control names (obligating document, receiving
   report, invoice, reconciliation workpaper, SF-50, access-recert listing, …) or
   run its check command (Bash). If the procedure says to sample, state your sample
   size and how many exceptions you found. Reason step by step about whether the
   evidence satisfies the control objective for its assertion(s).
4. Call FiarAssess (path + id) with:
   - status = effective ONLY if the evidence clearly shows the control operated;
              deficient if the evidence shows it did not (any un-remediated
              exception makes the control deficient);
              not_applicable if the control does not apply to this entity;
   - evidence = the exact KSDs you examined and what they showed (quote the line);
   - sample_size and exceptions when you tested a sample.
5. If you set deficient, immediately call FiarFinding to record the NFR: condition
   (what you observed), criteria (the policy/standard/GAAP requirement), cause
   (root cause), effect (impact on the assertion/balance), recommendation, and a
   severity (deficiency / significant_deficiency / material_weakness).

Give a one-line summary: control id, the KSD evidence, and the status you set.
