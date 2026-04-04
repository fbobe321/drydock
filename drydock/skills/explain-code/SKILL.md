---
name: explain-code
description: Explain code in detail. What it does, how it works, why it's written that way.
allowed-tools: read_file grep glob
user-invocable: true
---

# Code Explanation

Explain the code specified in $ARGUMENTS (file path, function name, or concept).

## Approach
1. Find and read the relevant code
2. Explain at three levels:
   - **What**: One sentence summary of what this code does
   - **How**: Step-by-step walkthrough of the logic
   - **Why**: Design decisions, tradeoffs, alternatives
3. Call out any:
   - Non-obvious patterns or idioms
   - Potential gotchas or edge cases
   - Performance characteristics
   - Dependencies on other parts of the codebase

## Format
Keep explanations clear and concise. Use code snippets to reference specific lines.
Adjust detail level to the complexity — simple code gets a brief explanation,
complex algorithms get detailed walkthroughs.
