#!/usr/bin/env python3
"""speciate.py — evolutionary SPECIATION for self-distillation (framework rec 5).

The composition wall: packing many task→solution maps into ONE LoRA memorizes
(loss→0) but retrieves nothing (transfer→0) — a lethal variation operator. The
evolutionary fix is speciation: split the corpus into SKILL CLUSTERS ("species")
and train one adapter per cluster, so each adapter only has to hold coherent,
composable variation instead of the whole zoo.

This module does the clustering (stdlib TF-IDF cosine + greedy agglomerative) and
emits a per-cluster training manifest. Pure stdlib so it runs anywhere.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def tfidf_vectors(docs: list[str]) -> list[dict[str, float]]:
    """TF-IDF weight vectors for a set of documents (stdlib, no deps)."""
    toks = [_tokens(d) for d in docs]
    df: Counter[str] = Counter()
    for t in toks:
        df.update(set(t))
    n = max(1, len(docs))
    vecs: list[dict[str, float]] = []
    for t in toks:
        tf = Counter(t)
        length = max(1, len(t))
        vecs.append({
            w: (c / length) * math.log((1 + n) / (1 + df[w]) + 1)
            for w, c in tf.items()
        })
    return vecs


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[w] * b[w] for w in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def cluster(docs: list[str], threshold: float = 0.12) -> list[list[int]]:
    """Greedy single-pass agglomerative clustering: each doc joins the nearest
    existing cluster whose centroid similarity exceeds `threshold`, else seeds a
    new cluster. Returns lists of document indices. Order-stable and deterministic."""
    vecs = tfidf_vectors(docs)
    clusters: list[list[int]] = []
    centroids: list[dict[str, float]] = []

    def merge_into(cen: dict[str, float], v: dict[str, float], k: int) -> dict[str, float]:
        # running mean centroid
        out = dict(cen)
        for w, val in v.items():
            out[w] = (out.get(w, 0.0) * (k - 1) + val) / k
        return out

    for i, v in enumerate(vecs):
        best, bi = threshold, -1
        for ci, cen in enumerate(centroids):
            s = cosine(v, cen)
            if s > best:
                best, bi = s, ci
        if bi < 0:
            clusters.append([i])
            centroids.append(dict(v))
        else:
            clusters[bi].append(i)
            centroids[bi] = merge_into(centroids[bi], v, len(clusters[bi]))
    return clusters


def speciate_corpus(records: list[dict]) -> list[dict]:
    """Cluster solved-trace records into species. Each record needs a "task" and
    some text to cluster on (we use task + first user instruction / messages).
    Returns [{species, tasks, size, member_indices}] sorted largest-first."""
    def doc_of(r: dict) -> str:
        parts = [str(r.get("task", ""))]
        for m in (r.get("messages") or [])[:3]:
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, str):
                parts.append(c)
        return " ".join(parts)

    docs = [doc_of(r) for r in records]
    groups = cluster(docs)
    species = []
    for k, idxs in enumerate(groups):
        species.append({
            "species": k,
            "tasks": [records[i].get("task", f"rec{i}") for i in idxs],
            "size": len(idxs),
            "member_indices": idxs,
        })
    species.sort(key=lambda s: s["size"], reverse=True)
    for k, s in enumerate(species):
        s["species"] = k
    return species


def write_manifests(records: list[dict], species: list[dict], out_dir: str) -> list[str]:
    """Write one JSONL training shard per species → out_dir/species_<k>.jsonl.
    Returns the shard paths. Each shard trains its OWN adapter (per-cluster)."""
    import os

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for s in species:
        p = os.path.join(out_dir, f"species_{s['species']}.jsonl")
        with open(p, "w") as f:
            for i in s["member_indices"]:
                f.write(json.dumps(records[i], default=str) + "\n")
        paths.append(p)
    return paths


def _load_jsonl(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


if __name__ == "__main__":
    # CLI: speciate.py <corpus.jsonl> [out_dir]  → prints species + writes shards
    if len(sys.argv) < 2:
        print("usage: speciate.py <corpus.jsonl> [out_dir]")
        raise SystemExit(2)
    recs = _load_jsonl(sys.argv[1])
    sp = speciate_corpus(recs)
    print(f"{len(recs)} traces → {len(sp)} species:")
    for s in sp:
        print(f"  species {s['species']}: {s['size']} tasks — {', '.join(s['tasks'][:6])}"
              + (" …" if s["size"] > 6 else ""))
    if len(sys.argv) > 2:
        paths = write_manifests(recs, sp, sys.argv[2])
        print(f"wrote {len(paths)} per-species shards → {sys.argv[2]}")
