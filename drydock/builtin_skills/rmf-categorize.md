---
name: rmf-categorize
description: Categorize a system (FIPS 199) and recommend a tailored 800-53 baseline
---
Help categorize a system for RMF Steps 1-3. If the user has not given the system
description and the data types it handles, ask for them first. Then:
1. Recommend a FIPS 199 security categorization (Low / Moderate / High) for
   confidentiality, integrity, and availability, each with a brief justification,
   and the resulting overall (high-water-mark) impact level.
2. Recommend the tailored initial NIST SP 800-53 baseline for that level — use the
   Knowledge tool to cite the relevant control families and key control IDs.
3. Note controls likely tailored OUT given the system boundary (e.g., wireless
   access controls for an air-gapped network).
Be concrete and cite control IDs.

System details: $ARGS
