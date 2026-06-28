---
name: rmf-review
description: Review an SSP implementation statement against 800-53A objectives
---
First call the Knowledge tool to retrieve the control statement and assessment
objectives (NIST SP 800-53A) for: $ARGS. Then review the implementation
statement(s) for those control(s): flag where they are incomplete, lack
specificity (who / what / how / how often), fail to address part of the control,
or omit continuous-monitoring requirements. Output: a one-line verdict
(adequate / needs work), then bulleted gaps, each mapped to the assessment
objective it fails to satisfy. Always consult the Knowledge tool before judging.
