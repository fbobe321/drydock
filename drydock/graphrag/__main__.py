"""GraphRAG CLI — `python -m drydock.graphrag <subcommand>`.

Subcommands:

    ingest <path>            Walk the path, index .py + .md/.txt into the DB.
    query <text>             Run a retrieval and print symbol+text hits.
    symbol <name>            Look up a class/function by name or qualname.
    chain <qualname>         Walk the inheritance chain for a class.
    stats                    Show counts.

Default DB path: $DRYDOCK_GRAPHRAG_DB or ~/.drydock/graphrag.sqlite
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from drydock.graphrag.storage import Index


def _default_db_path() -> str:
    env = os.environ.get("DRYDOCK_GRAPHRAG_DB")
    if env:
        return env
    return str(Path.home() / ".drydock" / "graphrag.sqlite")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drydock.graphrag",
        description="GraphRAG: code-graph + text retrieval for Drydock.",
    )
    p.add_argument(
        "--db",
        default=_default_db_path(),
        help="SQLite path (default: $DRYDOCK_GRAPHRAG_DB or ~/.drydock/graphrag.sqlite)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_ingest = sub.add_parser("ingest", help="Index a directory or file")
    sp_ingest.add_argument("path", help="Path to a project root or single file")

    sp_query = sub.add_parser("query", help="Retrieve mixed symbol+text hits")
    sp_query.add_argument("text", nargs="+", help="Query text")
    sp_query.add_argument("--text-limit", type=int, default=5)
    sp_query.add_argument("--symbol-limit", type=int, default=5)

    sp_sym = sub.add_parser("symbol", help="Look up a symbol by name")
    sp_sym.add_argument("name")
    sp_sym.add_argument("--limit", type=int, default=10)

    sp_chain = sub.add_parser(
        "chain", help="Walk the inheritance chain for a class"
    )
    sp_chain.add_argument("qualname")

    sub.add_parser("stats", help="Show DB counts")

    sp_we = sub.add_parser(
        "worked_example",
        help="Manage worked examples (problem + reasoning chain + answer)",
    )
    we_sub = sp_we.add_subparsers(dest="we_cmd", required=True)

    sp_we_add = we_sub.add_parser(
        "add", help="Add a worked example from a JSON file"
    )
    sp_we_add.add_argument(
        "json_path",
        help=(
            "Path to a JSON file with keys: problem_text, reasoning_steps "
            "(list[str]), final_answer, optional category, subject, source"
        ),
    )

    sp_we_list = we_sub.add_parser("list", help="List worked examples")
    sp_we_list.add_argument("--limit", type=int, default=20)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    idx = Index(args.db)

    if args.cmd == "ingest":
        counts = idx.ingest_path(args.path)
        print(
            f"Ingested {counts['files']} files: "
            f"{counts['symbols']} symbols, {counts['chunks']} text chunks."
        )
        print(f"DB: {args.db}")
        return 0

    if args.cmd == "query":
        query_text = " ".join(args.text)
        result = idx.retrieve(
            query_text,
            symbol_limit=args.symbol_limit,
            text_limit=args.text_limit,
        )
        print(result.format())
        return 0 if not result.is_empty() else 1

    if args.cmd == "symbol":
        hits = idx.find_symbol(args.name)[: args.limit]
        if not hits:
            print(f"No symbol found for '{args.name}'.")
            return 1
        for h in hits:
            print(h.format())
        return 0

    if args.cmd == "chain":
        chain = idx.inheritance_chain(args.qualname)
        if not chain:
            print(f"No class found for '{args.qualname}'.")
            return 1
        for i, h in enumerate(chain):
            indent = "  " * i
            arrow = "" if i == 0 else "↳ "
            print(f"{indent}{arrow}{h.format()}")
        return 0

    if args.cmd == "stats":
        s = idx.stats()
        print(
            f"symbols: {s['symbols']}\n"
            f"text chunks: {s['chunks']}\n"
            f"unique terms: {s['unique_terms']}\n"
            f"worked examples: {s.get('worked_examples', 0)}\n"
            f"DB: {args.db}"
        )
        return 0

    if args.cmd == "worked_example":
        if args.we_cmd == "add":
            import json as _json
            payload = _json.loads(Path(args.json_path).read_text())
            problem = payload.get("problem_text", "").strip()
            steps = payload.get("reasoning_steps", [])
            answer = payload.get("final_answer", "").strip()
            if not problem or not answer:
                print("ERROR: problem_text and final_answer are required",
                      file=sys.stderr)
                return 2
            eid = idx.ingest_worked_example(
                problem_text=problem,
                reasoning_steps=list(steps),
                final_answer=answer,
                category=payload.get("category", ""),
                subject=payload.get("subject", ""),
                source=payload.get("source", "manual"),
            )
            print(f"Ingested worked example id={eid} (source={payload.get('source','manual')})")
            return 0
        if args.we_cmd == "list":
            hits = idx.list_worked_examples(limit=args.limit)
            if not hits:
                print("(no worked examples)")
                return 1
            for h in hits:
                print(h.format())
                print()
            return 0
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
