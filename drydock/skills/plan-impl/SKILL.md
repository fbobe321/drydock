---
name: plan-impl
description: Create a detailed implementation plan before coding. Identifies files, dependencies, order of changes.
allowed-tools: read_file grep glob
context: fork
agent: planner
user-invocable: true
---

# Implementation Planning

Given a task description ($ARGUMENTS), create a comprehensive plan.

## Steps
1. Analyze the requirements
2. Explore the existing codebase to understand constraints
3. Identify all files that need to be created or modified
4. Determine the order of changes (dependencies first)
5. Estimate complexity per file

## Output Format
```
# Implementation Plan: [title]

## Overview
[1-2 sentence summary]

## Files to Change
1. `path/to/file.py` — [what changes] (complexity: low/med/high)
2. `path/to/other.py` — [what changes] (complexity: low/med/high)

## New Files
1. `path/to/new.py` — [purpose]

## Order of Implementation
1. [first thing to do and why]
2. [second thing]
3. [etc]

## Risks & Considerations
- [potential issues]

## Testing Strategy
- [how to verify the changes work]
```
