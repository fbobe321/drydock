"""RMF typed ontology graph — schema-typed nodes + relationships for RMF
traceability (Phase 2 of Operation RMF Automata).

Clean-room, stdlib only: a small in-memory typed graph persisted as JSON (NOT an
external Neo4j) so it stays local-first and dependency-free. It complements the
GraphRAG text KB: GraphRAG answers "what does the doc say", this answers "trace
the relationships" — e.g. which controls a component inherits from its parent.

Node types:  Control · Objective (assessment objective / test case) · Component ·
             Vulnerability · STIG · STIGRule · Boundary
Edge types:  ASSESSES (Objective→Control) · IMPLEMENTS (Component→Control) ·
             RESIDES_ON (Component→Component/Boundary) · AFFECTS (Vuln→Component) ·
             SATISFIED_BY (Control→STIGRule) · EVALUATES (STIGRule→Component) ·
             PART_OF (STIGRule→STIG) · APPLIES_TO (STIG→Component) · MITIGATES

All logic original to Drydock.
"""
from __future__ import annotations

import json
from pathlib import Path


def graph_path(cwd: str) -> Path:
    return Path(cwd) / ".drydock" / "rmf" / "graph.json"


class RmfGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}   # id -> {type, attrs}
        self.edges: list[dict] = []        # {src, rel, dst}
        self._edge_keys: set[tuple] = set()

    # ── mutation ────────────────────────────────────────────────────────────
    def add_node(self, nid: str, ntype: str, **attrs) -> str:
        nid = nid.strip()
        node = self.nodes.setdefault(nid, {"type": ntype, "attrs": {}})
        node["type"] = ntype
        node["attrs"].update({k: v for k, v in attrs.items() if v is not None})
        return nid

    def add_edge(self, src: str, rel: str, dst: str) -> None:
        key = (src, rel, dst)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append({"src": src, "rel": rel, "dst": dst})

    # ── queries ─────────────────────────────────────────────────────────────
    def get(self, nid: str) -> dict | None:
        n = self.nodes.get(nid)
        return {"id": nid, **n} if n else None

    def of_type(self, ntype: str) -> list[str]:
        return [nid for nid, n in self.nodes.items() if n["type"] == ntype]

    def neighbors(self, nid: str, rel: str | None = None, *, direction: str = "out") -> list[str]:
        out = []
        for e in self.edges:
            if rel and e["rel"] != rel:
                continue
            if direction == "out" and e["src"] == nid:
                out.append(e["dst"])
            elif direction == "in" and e["dst"] == nid:
                out.append(e["src"])
        return out

    def inherited_controls(self, component: str) -> list[str]:
        """Controls a component inherits from its ancestors (RESIDES_ON, transitive).
        The PRD's "which servers inherit physical controls" / inheritance logic."""
        inherited: list[str] = []
        seen: set[str] = set()
        frontier = self.neighbors(component, "RESIDES_ON", direction="out")
        while frontier:
            parent = frontier.pop()
            if parent in seen:
                continue
            seen.add(parent)
            inherited.extend(self.neighbors(parent, "IMPLEMENTS", direction="out"))
            frontier.extend(self.neighbors(parent, "RESIDES_ON", direction="out"))
        # de-dup, preserve order
        s: set[str] = set()
        return [c for c in inherited if not (c in s or s.add(c))]

    # ── persistence ─────────────────────────────────────────────────────────
    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"nodes": self.nodes, "edges": self.edges}), "utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "RmfGraph":
        g = cls()
        p = Path(path)
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
                g.nodes = data.get("nodes", {})
                for e in data.get("edges", []):
                    g.add_edge(e["src"], e["rel"], e["dst"])
            except (OSError, ValueError, KeyError):
                pass
        return g

    def stats(self) -> dict:
        by_type: dict[str, int] = {}
        for n in self.nodes.values():
            by_type[n["type"]] = by_type.get(n["type"], 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges), "by_type": by_type}


# ── id helpers (stable, typed) ──────────────────────────────────────────────
def control_id(cid: str) -> str:
    return f"control:{cid.lower()}"


def component_id(name: str) -> str:
    return f"component:{name.strip().lower()}"


def stig_id(name: str) -> str:
    return f"stig:{name.strip().lower()}"


def rule_node(rid: str) -> str:
    return f"stigrule:{rid.strip().lower()}"


def ingest_checklist(g: "RmfGraph", checklist) -> dict:
    """Add a parsed STIG Checklist to the typed graph: STIG + STIG-Rule nodes,
    PART_OF (rule→stig), APPLIES_TO (stig→host), EVALUATES (rule→host). Returns
    {rules, host}. The Control —SATISFIED_BY→ STIG-Rule link is asserted
    separately (via GraphAdd 'satisfies') since it needs the CCI→control map."""
    host = (checklist.asset.get("HOST_NAME") or checklist.asset.get("host_name") or "target")
    hnode = component_id(host)
    g.add_node(hnode, "Component", name=host, ip=checklist.asset.get("HOST_IP"))
    sname = checklist.stig_name or "STIG"
    snode = stig_id(sname)
    g.add_node(snode, "STIG", name=sname, version=checklist.stig_version)
    g.add_edge(snode, "APPLIES_TO", hnode)
    for r in checklist.rules:
        rid = r.rule_id or r.group_id
        if not rid:
            continue
        rn = rule_node(rid)
        g.add_node(rn, "STIGRule", rule_id=r.rule_id, group_id=r.group_id,
                   severity=r.severity, status=r.status, cci=r.cci, title=r.title)
        g.add_edge(rn, "PART_OF", snode)
        g.add_edge(rn, "EVALUATES", hnode)
    return {"rules": len(checklist.rules), "host": host}


def build_from_catalog(catalog: dict, *, families: list[str] | None = None) -> RmfGraph:
    """Deterministically build Control + Objective nodes (and ASSESSES edges) from
    an OSCAL 800-53 catalog. The typed backbone the user's components/vulns attach to."""
    g = RmfGraph()
    want = {f.lower() for f in families} if families else None
    for group in catalog.get("catalog", {}).get("groups", []) or []:
        fid = group.get("id", "")
        if want is not None and fid.lower() not in want:
            continue
        family = group.get("title", fid)
        for control in group.get("controls", []) or []:
            _add_control(g, control, family)
            for enh in control.get("controls", []) or []:
                _add_control(g, enh, family, parent=control.get("id"))
    return g


def _add_control(g: RmfGraph, control: dict, family: str, parent: str | None = None) -> None:
    cid = control.get("id", "")
    if not cid:
        return
    node = control_id(cid)
    g.add_node(node, "Control", control_id=cid.upper(), title=control.get("title", ""),
               family=family)
    if parent:
        g.add_edge(node, "ENHANCES", control_id(parent))
    params = {p["id"]: p.get("label") for p in control.get("params", []) or []}
    for i, part in enumerate(control.get("parts", []) or []):
        if part.get("name") == "assessment-objective":
            from drydock.rmf import _part_prose
            prose = _part_prose(part, params)
            if prose:
                oid = f"objective:{cid.lower()}:{i}"
                g.add_node(oid, "Objective", prose=prose[:500])
                g.add_edge(oid, "ASSESSES", node)
