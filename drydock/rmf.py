"""RMF automation — NIST SP 800-53 catalog ingestion for GraphRAG.

Local-first: fetch the machine-readable OSCAL catalog once (over the network),
flatten each control to readable text (id, family, statement, guidance,
assessment objective), and ingest it into the project GraphRAG knowledge base so
the agent can look up and reason over controls offline via the `Knowledge` tool.

Pairs with the bundled RMF skills (rmf-categorize / rmf-review / rmf-poam /
rmf-control) and the user's own ingested SSP / POA&M / scan artifacts.

Stdlib only (urllib + json). All logic original to Drydock.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

# NIST's official OSCAL content (public domain). Rev 5 catalog.
DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
    "nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"
)

_INSERT = re.compile(r"\{\{\s*insert:\s*param,\s*([A-Za-z0-9_.\-]+)\s*\}\}")
# Part names worth surfacing to the model, in order, with a readable label.
_PART_LABELS = [
    ("statement", "Statement"),
    ("guidance", "Guidance"),
    ("assessment-objective", "Assessment objective"),
]


def fetch_catalog(dest: str | Path, *, url: str = DEFAULT_CATALOG_URL,
                  timeout: float = 60.0) -> Path:
    """Download the OSCAL catalog JSON to dest. Returns the path."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "drydock-rmf"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (NIST, trusted)
        dest.write_bytes(r.read())
    return dest


def _resolve(prose: str, params: dict) -> str:
    """Replace OSCAL {{ insert: param, id }} markers with the param's label."""
    def sub(m):
        p = params.get(m.group(1))
        return f"[{p}]" if p else "[assignment]"
    return _INSERT.sub(sub, prose or "")


def _part_prose(part: dict, params: dict) -> str:
    """All prose in a part and its sub-parts, joined."""
    bits = []
    if part.get("prose"):
        bits.append(_resolve(part["prose"], params))
    for sub in part.get("parts", []) or []:
        t = _part_prose(sub, params)
        if t:
            bits.append(t)
    return "\n".join(bits).strip()


def flatten_control(control: dict, family: str) -> str:
    """Render one control as a readable text block for ingestion."""
    params = {p["id"]: p.get("label") or (p.get("select") or {}).get("how-many")
              for p in control.get("params", []) or []}
    cid = control.get("id", "").upper()
    lines = [f"{cid} — {control.get('title', '')}  (Family: {family})"]
    parts = {p.get("name"): p for p in control.get("parts", []) or []}
    for name, label in _PART_LABELS:
        if name in parts:
            prose = _part_prose(parts[name], params)
            if prose:
                lines.append(f"{label}: {prose}")
    return "\n".join(lines)


def catalog_to_family_docs(catalog: dict, dest_dir: str | Path,
                           families: list[str] | None = None) -> list[Path]:
    """Write one markdown doc per control family (e.g. ac.md). Returns paths.
    `families` (lowercase ids like 'ac') filters which to write; None = all."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    want = {f.lower() for f in families} if families else None
    written: list[Path] = []
    for group in catalog.get("catalog", {}).get("groups", []) or []:
        fid = group.get("id", "")
        if want is not None and fid.lower() not in want:
            continue
        title = group.get("title", fid)
        blocks = [f"# {title} ({fid.upper()}) — NIST SP 800-53 Rev 5\n"]
        for control in group.get("controls", []) or []:
            blocks.append(flatten_control(control, title))
            # nested enhancements
            for enh in control.get("controls", []) or []:
                blocks.append(flatten_control(enh, title))
        out = dest_dir / f"{fid}.md"
        out.write_text("\n\n".join(blocks), encoding="utf-8")
        written.append(out)
    return written


def rmf_dir(cwd: str) -> Path:
    return Path(cwd) / ".drydock" / "rmf"


def bootstrap(cwd: str, *, families: list[str] | None = None,
              source: str | Path | None = None, refresh: bool = False) -> dict:
    """Fetch (if needed) + flatten the 800-53 catalog into <cwd>/.drydock/rmf and
    ingest it into the GraphRAG KB. Returns {families, controls_docs, **kb_stats}.
    `source` reuses an already-downloaded catalog JSON (offline). `refresh` pulls
    the latest from upstream, but falls back to the cached catalog if upstream is
    unreachable — so a network blip never stalls an assessment (PRD §4)."""
    from drydock import graphrag

    base = rmf_dir(cwd)
    cat_path = Path(source) if source else base / "catalog.json"
    if source is None and (refresh or not cat_path.exists()):
        try:
            fetch_catalog(cat_path)
        except Exception:  # noqa: BLE001 — any network/HTTP failure
            if not cat_path.exists():
                raise  # nothing cached to fall back to
            # else: keep using the cached catalog
    catalog = json.loads(Path(cat_path).read_text("utf-8"))
    docs = catalog_to_family_docs(catalog, base / "800-53", families)
    store = graphrag.default_store_path(cwd)
    stats = graphrag.add_to_index([str(d) for d in docs], store, cwd=cwd)
    stats["family_docs"] = len(docs)
    # Phase 2: also build the typed ontology graph (Control + Objective backbone)
    # so the agent can TRACE relationships via GraphQuery, not just retrieve text.
    from drydock import rmf_graph
    g = rmf_graph.build_from_catalog(catalog, families=families)
    g.save(rmf_graph.graph_path(cwd))
    stats["graph"] = g.stats()
    return stats
