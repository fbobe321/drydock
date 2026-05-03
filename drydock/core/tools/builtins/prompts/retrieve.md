Query the project's persistent GraphRAG index for code symbols and prose chunks.

When to use:
- BEFORE editing a file you haven't read, especially in an unfamiliar codebase
- Finding where a class/function is defined (works across packages — e.g. `is_json` is in `werkzeug.wrappers.Request`, not `flask.wrappers.Request`)
- Finding inheritance chains (`retrieve(query="Request")` surfaces parent classes)
- Looking up a topic from project docs / markdown (PRDs, design notes, READMEs)

Examples:
- `retrieve(query="is_json")` — symbol lookup, returns definition site + parent chain
- `retrieve(query="Request")` — finds all `Request` classes across packages
- `retrieve(query="how does the cache invalidate")` — prose lookup over markdown
- `retrieve(query="auth flow", text_limit=3, symbol_limit=2)` — limit hits

Returns:
- Symbol hits: `<kind> <qualname> at <file>:<line> (extends <parents>)`
- Text hits: `<file>:<start>-<end> (score=<float>)\n<chunk content>`
- Or a "no index found" hint with the command to create one.

If no index exists for this project, the tool returns instructions for running `python -m drydock.graphrag ingest .` first. The model should report that to the user rather than guessing at file contents.

Use this BEFORE `read_file` when you don't already know the path — it's much faster than `grep` for "where is X defined" questions.
