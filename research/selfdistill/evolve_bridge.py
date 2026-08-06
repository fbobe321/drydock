#!/usr/bin/env python3
"""evolve_bridge.py — stateful decision seam between the bash ratchet driver and
the tested evolutionary core in drydock.ratchet. The driver calls `record` once
per round with the round's fitness; the bridge persists a Quality-Diversity
archive + lineage + variation-policy state in a JSON file and returns what to do
next (next operator, params, any crossover plan, and whether we're done).

Keeping this in Python (not bash) means the whole evolutionary loop — archive
niching, Pareto selection, stall escalation, crossover pairing — is unit-testable
offline, with no GPU. Imports drydock.ratchet via PYTHONPATH.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.environ.get("DRYDOCK_SRC", "/data3/drydock-v3"))
from drydock.ratchet import (  # noqa: E402
    Archive,
    Candidate,
    VariationPolicy,
    plan_crossover,
)


def _load(state_path: str) -> dict:
    if os.path.exists(state_path):
        return json.load(open(state_path))
    return {"elites": [], "policy": {"stall": 0, "rung": 0, "patience": 1, "fanout": 3},
            "lineage": [], "gen": 0}


def _cand_from(d: dict) -> Candidate:
    return Candidate(
        id=d["id"], fitness=d["fitness"], total=d["total"], snapshot=d.get("snapshot"),
        descriptor=frozenset(d["descriptor"]) if d.get("descriptor") is not None else None,
        parents=tuple(d.get("parents", ())), generation=d.get("generation", 0),
        cost=d.get("cost", 0.0), generalizes=d.get("generalizes", 0.0),
    )


def _cand_to(c: Candidate) -> dict:
    return {
        "id": c.id, "fitness": c.fitness, "total": c.total, "snapshot": c.snapshot,
        "descriptor": sorted(c.descriptor) if c.descriptor is not None else None,
        "parents": list(c.parents), "generation": c.generation,
        "cost": c.cost, "generalizes": c.generalizes,
    }


def _rebuild(state: dict) -> tuple[Archive, VariationPolicy]:
    arch = Archive()
    for d in state["elites"]:
        arch.consider(_cand_from(d))
    p = state["policy"]
    pol = VariationPolicy(patience=p.get("patience", 1), fanout=p.get("fanout", 3))
    pol.stall, pol.rung = p.get("stall", 0), p.get("rung", 0)
    return arch, pol


def record(state_path: str, payload: dict) -> dict:
    """payload: {id, passed, total, descriptor|null, snapshot, cost}. Update the
    archive + policy, decide the next move, persist, and return the decision."""
    state = _load(state_path)
    arch, pol = _rebuild(state)
    prev_best = arch.best()
    prev_bp = prev_best.fitness if prev_best else -1

    c = _cand_from({
        "id": payload["id"], "fitness": payload["passed"], "total": payload["total"],
        "snapshot": payload.get("snapshot"), "descriptor": payload.get("descriptor"),
        "parents": payload.get("parents", []), "generation": state["gen"],
        "cost": payload.get("cost", 0.0),
    })
    action = arch.consider(c)
    state["gen"] += 1
    state["lineage"].append({**_cand_to(c), "action": action})

    best = arch.best()
    improved = best.fitness > prev_bp
    pairs = arch.complementary_pairs()
    have_x = bool(pairs)
    op = pol.next_operator(improved=improved, have_crossover=have_x)
    xplan = None
    if op == "crossover" and pairs:
        xplan = plan_crossover(pairs[0][0], pairs[0][1], state["gen"])

    # persist
    state["elites"] = [_cand_to(e) for e in arch.all()]
    state["policy"] = {"stall": pol.stall, "rung": pol.rung,
                       "patience": pol.patience, "fanout": pol.fanout}
    json.dump(state, open(state_path, "w"), indent=0)

    solved = arch.solved()
    return {
        "action": action,
        "operator": op,
        "params": pol.params_for(op),
        "improved": improved,
        "best_ref": best.snapshot if best else None,
        "best_passed": best.fitness if best else -1,
        "best_total": best.total if best else 0,
        "niches": len(arch.all()),
        "solved": bool(solved),
        "solved_ref": solved.snapshot if solved else None,
        "crossover": xplan,
    }


if __name__ == "__main__":
    # usage: evolve_bridge.py record <state.json> <payload.json>
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "record":
        state_path, payload_path = sys.argv[2], sys.argv[3]
        out = record(state_path, json.load(open(payload_path)))
        print(json.dumps(out))
    else:
        print("usage: evolve_bridge.py record <state.json> <payload.json>", file=sys.stderr)
        raise SystemExit(2)
