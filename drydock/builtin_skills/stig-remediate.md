---
name: stig-remediate
description: Generate a remediation script for an Open STIG finding
---
Generate a remediation script for an Open STIG finding. Input: $ARGS (a checklist
path and a rule id, e.g. "sys.ckl V-230222").

1. Call StigRule (the path + rule_id) to read the rule's Fix Text and Check Content.
2. Determine the target OS — infer from the STIG/checklist name, or ask if unclear.
3. Translate the prose Fix Text into an IDEMPOTENT, executable remediation script in
   the requested format (default: Bash for Linux, PowerShell for Windows; Ansible if
   asked). It must bring the system into compliance with the Check Content. Start it
   with a comment header citing the rule id and what it fixes.
4. Write the script to a file (Write tool), e.g. remediate_<rule>.sh, and tell the
   user the path. Do NOT run it — the operator reviews and runs remediation.
Only GENERATE the script; never execute remediation yourself.
