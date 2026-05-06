#!/usr/bin/env python3
"""Fetch arXiv abstracts via OAI-PMH and write chunked .txt files for
GraphRAG ingestion.

Output: /data3/arxiv_corpus/raw/<set>/batch_<N>.txt
  - 100 records per file
  - Each record: ===ID=== / TITLE / CATEGORIES / ABSTRACT, blank-line separated

State: /data3/arxiv_corpus/state/<set>.json — resumption token + batch counter.
Resumable: re-running picks up from saved token.

Stdlib only. Polite: 3s between requests, exp backoff on 503.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path("/data3/arxiv_corpus")
RAW = ROOT / "raw"
STATE = ROOT / "state"
ENDPOINT = "https://oaipmh.arxiv.org/oai"
NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "ax": "http://arxiv.org/OAI/arXiv/",
}
RECORDS_PER_FILE = 100
SLEEP_BETWEEN = 3.0  # arXiv asks for ≥ 3s between requests
MAX_BACKOFF = 300.0


def _state_path(setname: str) -> Path:
    return STATE / f"{setname}.json"


def _load_state(setname: str) -> dict:
    p = _state_path(setname)
    if p.exists():
        return json.loads(p.read_text())
    return {"set": setname, "token": None, "batch": 0, "records": 0,
            "buffer": [], "done": False}


def _save_state(state: dict) -> None:
    p = _state_path(state["set"])
    p.write_text(json.dumps(state))


def _write_chunk(state: dict, chunk: list[str]) -> None:
    if not chunk:
        return
    out_dir = RAW / state["set"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"batch_{state['batch']:06d}.txt"
    out.write_text("\n\n".join(chunk), encoding="utf-8")
    state["batch"] += 1


def _fetch(url: str) -> bytes:
    backoff = 5.0
    while True:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "drydock-graphrag/0.1 (+local research)"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                if resp.status == 200:
                    return resp.read()
                # 503 with Retry-After is common
                retry = resp.headers.get("Retry-After")
                wait = float(retry) if retry else backoff
                print(f"[fetch] HTTP {resp.status}, sleeping {wait}s",
                      file=sys.stderr, flush=True)
                time.sleep(min(wait, MAX_BACKOFF))
        except urllib.error.HTTPError as e:
            retry = e.headers.get("Retry-After") if e.headers else None
            wait = float(retry) if retry else backoff
            print(f"[fetch] HTTPError {e.code}, sleeping {wait}s",
                  file=sys.stderr, flush=True)
            time.sleep(min(wait, MAX_BACKOFF))
            backoff = min(backoff * 2, MAX_BACKOFF)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[fetch] {type(e).__name__}: {e}, sleeping {backoff}s",
                  file=sys.stderr, flush=True)
            time.sleep(min(backoff, MAX_BACKOFF))
            backoff = min(backoff * 2, MAX_BACKOFF)


def _parse_records(xml_bytes: bytes) -> tuple[list[str], str | None, bool]:
    """Return (record_blocks, next_token, done).

    Records are formatted plain-text blocks ready to write to disk.
    """
    root = ET.fromstring(xml_bytes)
    err = root.find("oai:error", NS)
    if err is not None:
        code = err.get("code", "unknown")
        if code == "noRecordsMatch":
            return [], None, True
        raise RuntimeError(f"OAI error {code}: {err.text}")

    list_records = root.find("oai:ListRecords", NS)
    if list_records is None:
        return [], None, True

    blocks: list[str] = []
    for rec in list_records.findall("oai:record", NS):
        meta = rec.find("oai:metadata", NS)
        if meta is None:
            continue
        ax = meta.find("ax:arXiv", NS)
        if ax is None:
            continue
        ax_id = (ax.findtext("ax:id", "", NS) or "").strip()
        title = (ax.findtext("ax:title", "", NS) or "").strip()
        cats = (ax.findtext("ax:categories", "", NS) or "").strip()
        abstract = (ax.findtext("ax:abstract", "", NS) or "").strip()
        if not (ax_id and abstract):
            continue
        # Normalize whitespace inside title/abstract
        title = " ".join(title.split())
        abstract = " ".join(abstract.split())
        block = (
            f"===arxiv:{ax_id}===\n"
            f"TITLE: {title}\n"
            f"CATEGORIES: {cats}\n"
            f"ABSTRACT: {abstract}"
        )
        blocks.append(block)

    token_el = list_records.find("oai:resumptionToken", NS)
    next_token = (token_el.text or "").strip() if token_el is not None else None
    if not next_token:
        next_token = None
    done = next_token is None
    return blocks, next_token, done


def fetch_set(setname: str, max_records: int | None = None) -> None:
    state = _load_state(setname)
    if state.get("done"):
        print(f"[{setname}] already done ({state['records']} records)")
        return

    while True:
        if state["token"]:
            url = f"{ENDPOINT}?verb=ListRecords&resumptionToken={state['token']}"
        else:
            url = (f"{ENDPOINT}?verb=ListRecords&set={setname}"
                   "&metadataPrefix=arXiv")

        print(f"[{setname}] fetch (records so far: {state['records']}, "
              f"batch: {state['batch']}, token: {state['token']})",
              flush=True)
        xml = _fetch(url)
        blocks, next_token, done = _parse_records(xml)
        state["buffer"].extend(blocks)
        state["records"] += len(blocks)

        # Flush full files
        while len(state["buffer"]) >= RECORDS_PER_FILE:
            chunk = state["buffer"][:RECORDS_PER_FILE]
            state["buffer"] = state["buffer"][RECORDS_PER_FILE:]
            _write_chunk(state, chunk)

        state["token"] = next_token
        if done:
            state["done"] = True
            _write_chunk(state, state["buffer"])
            state["buffer"] = []
            _save_state(state)
            print(f"[{setname}] DONE: {state['records']} records "
                  f"in {state['batch']} files", flush=True)
            return

        _save_state(state)
        if max_records is not None and state["records"] >= max_records:
            print(f"[{setname}] reached max_records={max_records}", flush=True)
            return

        time.sleep(SLEEP_BETWEEN)


def main() -> int:
    sets = sys.argv[1:] or [
        "math", "physics", "cs", "q-bio", "stat", "eess", "q-fin", "econ",
    ]
    for s in sets:
        try:
            fetch_set(s)
        except KeyboardInterrupt:
            print(f"[{s}] interrupted, state saved", flush=True)
            return 130
        except Exception as e:
            print(f"[{s}] FAILED: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
