---
name: perf-analyze
description: Performance analysis. Profile code, find bottlenecks, suggest optimizations.
allowed-tools: bash read_file grep glob
user-invocable: true
---

# Performance Analysis

Analyze code performance for $ARGUMENTS (or recent changes).

## Steps
1. Identify hot paths — which functions are called most?
2. Look for common performance issues:
   - N+1 queries (loops with database calls)
   - Unnecessary list copies/conversions
   - Missing caching for repeated computations
   - Blocking I/O in async code
   - Large memory allocations in loops
   - String concatenation in loops (use join)
3. Profile if possible: `python -m cProfile -s cumtime script.py 2>&1 | head -30`
4. Suggest specific optimizations with before/after examples

## Output
### Bottlenecks Found
- [file:line] Description — estimated impact

### Recommended Optimizations
1. [Change] — expected improvement
