---
name: rmf-poam
description: Generate a POA&M entry from a finding / vulnerability / failed STIG
---
First call the Knowledge tool to map this finding to the impacted system
component(s) and the specific NIST SP 800-53 control(s) it makes non-compliant:
$ARGS. Then generate a formatted POA&M (Plan of Action & Milestones) entry with:
Weakness description; Source (scan / STIG / assessment); Affected component(s);
Non-compliant control(s); Severity; Recommended remediation with concrete
technical milestones; estimated remediation timeline. Be specific (e.g., "Apply
patch X to Server Y to satisfy SI-2"). Consult the Knowledge tool before writing.
