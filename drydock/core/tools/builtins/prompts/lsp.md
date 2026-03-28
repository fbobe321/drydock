Language Server Protocol integration.

- `lsp(action="diagnostics", file="src/main.py")` — Type check a file
- `lsp(action="definition", file="src/main.py", line=10, column=5)` — Go to definition
- `lsp(action="references", file="src/main.py", symbol="my_func")` — Find all references
- `lsp(action="symbols", file="src/main.py")` — List classes and functions
- `lsp(action="hover", file="src/main.py", line=10, column=5)` — Get type info

Requires pyright for full diagnostics. Falls back to grep-based search.
