# Local Document Q&A System with Semantic Search

## Project

A local, offline-capable document question-answering system that lets a user
ingest documents, index them semantically, and ask natural-language questions
that get answered with passages drawn from those documents.

This is a small RAG (Retrieval-Augmented Generation) pipeline meant to run
entirely on a local machine with no cloud dependencies.

## Package Name

`doc_qa_system`

## Required Files

```
doc_qa_system/
├── __init__.py
├── __main__.py        # entry point — `python3 -m doc_qa_system <subcommand>`
├── ingestion.py       # text extraction + chunking from .txt/.md
├── embeddings.py      # builds and stores vector embeddings
├── search.py          # semantic search over the vector store
├── qa.py              # combines search results into an answer
└── cli.py             # argparse subcommands: ingest, query, list, clear
```

`__init__.py` SHOULD only re-export public symbols from the sibling modules in
this package. Do not import from a `core/` subpackage that does not exist.

`__main__.py` MUST call the CLI entry point so that
`python3 -m doc_qa_system --help` actually prints help. Use this exact pattern:

```python
from doc_qa_system.cli import main

if __name__ == "__main__":
    main()
```

## Subcommands (CLI)

The package MUST support these subcommands via `python3 -m doc_qa_system`:

| Subcommand | What it does                                              |
|------------|-----------------------------------------------------------|
| `ingest <dir>` | Read every `.txt`/`.md` file in a directory, chunk each into ~500-character pieces, build embeddings, persist to disk. |
| `query "<question>"` | Embed the question, do nearest-neighbour lookup against the stored chunks, print the top 3 chunks plus a 1–3 sentence synthesised answer. |
| `list`     | Print every ingested document path and how many chunks it contributed.    |
| `clear`    | Delete all ingested data.                                                 |

`python3 -m doc_qa_system --help` MUST exit 0 and print help that mentions all
four subcommands.

## Storage

Embeddings and metadata go under `./.doc_qa_data/` in the project directory.
The format is up to the implementer — JSON, pickle, FAISS, or a simple list of
numpy arrays are all fine.

## Test Cases

```
mkdir -p /tmp/qa_docs
echo "The capital of France is Paris."     > /tmp/qa_docs/a.txt
echo "Mount Everest is the tallest mountain in the world."  > /tmp/qa_docs/b.txt

python3 -m doc_qa_system --help                  → exit 0, mentions all subcommands
python3 -m doc_qa_system ingest /tmp/qa_docs     → exit 0, reports ingested 2 files
python3 -m doc_qa_system list                    → prints both paths, exit 0
python3 -m doc_qa_system query "What is the capital of France?" → exit 0, output mentions Paris
python3 -m doc_qa_system clear                   → exit 0
```

## Constraints

- Pure Python ≥ 3.10. Standard library only is fine for an MVP — you do not
  need faiss, sentence-transformers, or any heavyweight dependency. A simple
  TF-IDF or even bag-of-words representation is acceptable for this exercise.
- Each subcommand MUST be wired through to a working handler — not just
  registered in argparse.
- Imports MUST resolve. Every `from .x import Y` you write means `x.py` must
  also exist in the same package directory.
- After writing all files, run `python3 -m doc_qa_system --help` once to
  verify the package is importable and the entry point works.
