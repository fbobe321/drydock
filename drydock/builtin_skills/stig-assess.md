---
name: stig-assess
description: Assess the next un-reviewed STIG rule against system evidence
---
Assess STIG rules in the checklist at: $ARGS

Do exactly ONE rule per run (loop it — /loop N /stig-assess <path> — for the
whole checklist):

1. Call StigRules with that path and status=not_reviewed. If none remain, say the
   checklist is fully assessed and STOP without calling other tools.
2. Take the FIRST not_reviewed rule. Call StigRule (path + rule_id) to read its
   Check Content and Fix Text.
3. You MUST gather fresh evidence before deciding — do NOT guess. Read the exact
   configuration file the Check Content names (Read tool) or run its check command
   (Bash). Quote the specific line(s) you found. Reason step by step about whether
   that evidence satisfies the Check Content.
4. Call StigSet (path + rule_id) with:
   - status = not_a_finding ONLY if the evidence clearly shows compliance;
              open if the evidence shows non-compliance;
              not_applicable if the rule does not apply to this system;
   - finding_details = the exact evidence you observed and why it does/doesn't
     comply (quote the config line).
Give a one-line summary: rule id, the evidence line, and the status you set.
