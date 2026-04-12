# Worked Examples

Canonical skeletons for hard problems Gemma 4 consistently struggles with.
When drydock gets stuck on a difficult algorithm, the meta_ralph_loop
auto-injects the relevant example as a hint.

## Files

| File | Use when PRD needs |
|---|---|
| `sql_parser.py` | Tokenizer + recursive-descent for SELECT/INSERT/UPDATE/DELETE |
| `tree_interp.py` | Tree-walking interpreter with env, closures, control flow |
| `depgraph.py` | Topological sort + cycle detection for dependency resolution |
| `btree.py` | B-tree insert/search/serialize |
| `recursive_parser.py` | Generic recursive descent pattern |
| `subprocess_sandbox.py` | Subprocess with timeout, stdin JSON, stdout JSON |

## Lookup

`lookup.json` maps PRD keywords to relevant example files. The stuck
detector greps the PRD/failing-test output and surfaces the best match.
