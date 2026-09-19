"""Context replay and diff — reconstruct exactly what the model knew on a given call.

Validation PRD §29/§30/§31. The operator's note: "the biggest thing I'd add is not
another algorithm — it's deterministic replay + context diffing."

That is earned. Three components this session reported success while doing nothing —
prefix telemetry recording all zeros, the ratchet bridge recording 1 of 3 rounds, the
modular window registering modules then degrading none. Each cost several live runs to
diagnose, because after the fact there was no way to ask what was actually resident.
Every one would have been a single `diff` away.

A call record is a MANIFEST: the ordered modules, their versions, their chosen
resolution levels, and their token sizes. Because the store is append-only and keeps
every version, a manifest is enough to rebuild the exact serialized context later —
no transcript archaeology required.

Pure/stdlib. Read-only: nothing here changes what any model sees.
"""
from __future__ import annotations

import json
import time

from drydock.context_runtime import ContextStore


def manifest_from_view(view, call_id: str, note: str = "") -> dict:
    """Capture a ContextView as a replayable manifest (§29)."""
    m = view.manifest()
    m.update({"call_id": str(call_id), "ts": time.time(), "note": note})
    return m


class CallLog:
    """Append-only log of context manifests, one row per inference call."""

    def __init__(self, store: ContextStore):
        self.store = store
        self.path = store.dir / "calls.jsonl"

    def record(self, manifest: dict) -> dict:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(manifest, default=str, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            pass
        return manifest

    def all(self) -> list:
        out: list = []
        try:
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return out

    def get(self, call_id: str) -> "dict | None":
        for r in self.all():
            if str(r.get("call_id")) == str(call_id):
                return r
        return None


def replay(store: ContextStore, manifest: dict, policy: str = "modular") -> str:
    """Rebuild the serialized context a call actually saw (§30).

    `policy="modular"` renders each module at the resolution recorded in the manifest —
    what the model really got. `policy="append"` renders every module at full text — what
    an append-only run would have sent — so a failure can be asked: was this the model,
    or did MCR hand it a worse context?
    """
    parts: list = []
    for entry in (manifest or {}).get("modules", []):
        cid = entry.get("id")
        mod = store.get_version(cid, int(entry.get("version") or 0)) or store.get(cid)
        if mod is None:
            parts.append(f"[{cid} — MISSING FROM STORE]")
            continue
        if policy == "append":
            lvl = max(mod.available_levels()) if mod.resolutions else None
        else:
            lvl = entry.get("level")
        parts.append(mod.text_at(lvl) if mod.resolutions else mod.body)
    return "\n\n".join(p for p in parts if p)


def diff(before: dict, after: dict) -> dict:
    """Differential context debugger (§31).

    Reports the unchanged prefix, what was added/removed, which modules were compacted to
    a cheaper resolution (with before→after token sizes), which changed version, and the
    token offset where cache invalidation begins — which is the number that explains a
    sudden latency spike.
    """
    b = {e["id"]: e for e in (before or {}).get("modules", [])}
    a = {e["id"]: e for e in (after or {}).get("modules", [])}
    b_order = [e["id"] for e in (before or {}).get("modules", [])]
    a_order = [e["id"] for e in (after or {}).get("modules", [])]

    # unchanged prefix: walk both orders while id, version AND level all match, since a
    # resolution change is different bytes even at the same version
    unchanged_tokens = 0
    idx = 0
    for x, y in zip(b_order, a_order):
        eb, ea = b[x], a[y]
        if (x != y or eb.get("version") != ea.get("version")
                or eb.get("level") != ea.get("level")):
            break
        unchanged_tokens += int(ea.get("tokens") or 0)
        idx += 1

    added = [i for i in a_order if i not in b]
    removed = [i for i in b_order if i not in a]
    compacted, changed = [], []
    for i in a_order:
        if i not in b:
            continue
        eb, ea = b[i], a[i]
        if eb.get("level") != ea.get("level"):
            compacted.append({"id": i, "from_level": eb.get("level"),
                              "to_level": ea.get("level"),
                              "from_tokens": eb.get("tokens"), "to_tokens": ea.get("tokens")})
        elif eb.get("version") != ea.get("version"):
            changed.append({"id": i, "from": eb.get("version"), "to": ea.get("version")})
    return {
        "unchanged_prefix_modules": idx,
        "unchanged_prefix_tokens": unchanged_tokens,
        "added": [{"id": i, "tokens": a[i].get("tokens")} for i in added],
        "removed": [{"id": i, "tokens": b[i].get("tokens")} for i in removed],
        "compacted": compacted,
        "changed": changed,
        "cache_invalidation_starts_at_token": (unchanged_tokens + 1
                                               if (added or removed or compacted or changed)
                                               else None),
    }


def render_diff(d: dict) -> str:
    """The §31 report, readable at a glance."""
    lines = [f"UNCHANGED PREFIX:\n  {d['unchanged_prefix_tokens']:,} tokens "
             f"({d['unchanged_prefix_modules']} modules)"]
    for key, label in (("added", "ADDED"), ("removed", "REMOVED")):
        for e in d.get(key) or []:
            lines.append(f"{label}:\n  {e['id']}  {int(e.get('tokens') or 0):,} tokens")
    for e in d.get("compacted") or []:
        lines.append(f"COMPACTED:\n  {e['id']}  "
                     f"{int(e.get('from_tokens') or 0):,} → {int(e.get('to_tokens') or 0):,} tokens "
                     f"(L{e['from_level']} → L{e['to_level']})")
    for e in d.get("changed") or []:
        lines.append(f"CHANGED:\n  {e['id']}  v{e['from']} → v{e['to']}")
    start = d.get("cache_invalidation_starts_at_token")
    lines.append("CACHE INVALIDATION:\n  " +
                 (f"starts at token {start:,}" if start else "none — prefix fully reusable"))
    return "\n".join(lines)
