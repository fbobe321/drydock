---
name: deep-research
description: Deep research a topic using web search, code exploration, and multi-step analysis. Produces a structured report.
user-invocable: true
allowed-tools:
  - bash
  - grep
  - read_file
  - webfetch
  - websearch
  - write_file
---

# Deep Research

Conduct thorough research on a topic and produce a structured report.

## Workflow

1. **Clarify scope**: What exactly needs to be researched? Ask if unclear.
2. **Web search**: Use `websearch` for current information, articles, docs.
3. **Code exploration**: Use `grep` and `read_file` to find relevant code patterns.
4. **Cross-reference**: Compare web findings with codebase reality.
5. **Write report**: Produce a structured markdown report with findings.

## Report Template

Write the report to `research_report.md`:

```markdown
# Research: [Topic]

## Summary
One paragraph overview of findings.

## Key Findings
- Finding 1 with source
- Finding 2 with source
- Finding 3 with source

## Code Analysis
Relevant code patterns found in the codebase.

## Recommendations
Actionable next steps based on research.

## Sources
- [Source 1](url)
- [Source 2](url)
```

## Rules

- Always cite sources (URLs for web, file:line for code)
- If websearch fails (SSL/network), fall back to code-only analysis
- Keep the report under 500 lines
- Focus on actionable findings, not background
