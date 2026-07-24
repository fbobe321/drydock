---
name: fiar-evidence
description: Trace the full FIAR evidence chain for a control before passing it
---
Substantively test ONE FIAR control by tracing the complete evidence chain, for the
engagement + control id in: $ARGS

A control cannot support a financial-statement assertion on a broken audit trail.
You MUST trace all eight links before FiarAssess will let you mark it effective:

  population → sample → source_transaction → authorization → supporting_document
            → system_posting → gl_effect → assertion

1. Call FiarControl (path + id) to read the objective, assertions and KSDs.
2. Establish the POPULATION: the recorded universe (a GL detail, a transaction
   register, an APSR listing). State how you know it is complete.
3. SELECT a sample from that population and record exactly which item(s).
4. For each sampled item, gather and Read the real evidence for each link:
   - source_transaction: the transaction the item represents;
   - authorization: the warranted/authorized approver (warrant, SF-50, delegation);
   - supporting_document: the KSD (contract, receiving report, invoice, recon wp);
   - system_posting: where it posted in the feeder/accounting system;
   - gl_effect: the resulting general-ledger entry;
   - assertion: the FS assertion it ultimately supports.
5. Call FiarAssess with status=effective and a `chain` object whose keys are the
   eight links above, each set to the specific evidence you found. If any link is
   missing, FiarAssess will REFUSE and name the gap — go get that evidence, do not
   force the status. If a link genuinely cannot be traced (e.g. no authorization on
   file), the control is DEFICIENT: set status=deficient and record an NFR with
   FiarFinding.

Report the item you traced and, link by link, the evidence that supported it (or the
link that broke the chain).
