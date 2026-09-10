"""Tests for swarm capacity detection (drydock/capacity.py) — no live server needed."""
from __future__ import annotations

import threading
import time

from drydock import capacity


def test_swarm_size_bounds_are_a_min():
    # small box: concurrency binds → tone down
    assert capacity.swarm_size(2, task_demand=6) == 2
    # big box: task-demand binds (never wasteful cloning past distinct approaches)
    assert capacity.swarm_size(64, task_demand=6) == 6
    # budget caps below both
    assert capacity.swarm_size(64, task_demand=6, budget_agents=3) == 3
    # hard_max ceiling
    assert capacity.swarm_size(9999, task_demand=9999, hard_max=32) == 32
    assert capacity.swarm_size(0) == 1  # never zero


def test_slots_concurrency_reads_llamacpp_slots():
    assert capacity.slots_concurrency("http://h:8000/v1",
                                      getter=lambda u: [{"id": 0}, {"id": 1}]) == 2
    assert capacity.slots_concurrency("http://h:8000/v1", getter=lambda u: None) is None
    assert capacity.slots_concurrency("http://h:8000/v1", getter=lambda u: []) is None


def test_slots_url_strips_v1():
    seen = {}

    def getter(u):
        seen["url"] = u
        return [{"id": 0}]

    capacity.slots_concurrency("http://box:8000/v1", getter=getter)
    assert seen["url"] == "http://box:8000/slots"


def test_detect_prefers_explicit_override(monkeypatch):
    # config override wins over everything (operator knows their 8-GPU box)
    assert capacity.detect_concurrency("http://x/v1", config={"swarm_concurrency": 32}) == 32
    # env override
    monkeypatch.setenv("DRYDOCK_SWARM_CONCURRENCY", "24")
    assert capacity.detect_concurrency("http://x/v1", config={}) == 24


def test_detect_order_slots_then_probe_then_default(monkeypatch):
    monkeypatch.delenv("DRYDOCK_SWARM_CONCURRENCY", raising=False)
    # slots hit
    monkeypatch.setattr(capacity, "slots_concurrency", lambda u, **k: 3)
    assert capacity.detect_concurrency("http://x/v1", model="m", config={}) == 3
    # no slots → empirical probe
    monkeypatch.setattr(capacity, "slots_concurrency", lambda u, **k: None)
    monkeypatch.setattr(capacity, "probe_concurrency", lambda u, m, **k: 7)
    assert capacity.detect_concurrency("http://x/v1", model="m", config={}) == 7
    # no slots, no model (can't probe) → default
    assert capacity.detect_concurrency("http://x/v1", model="", config={}) == capacity.DEFAULT_CONCURRENCY
    assert capacity.detect_concurrency("http://x/v1", model="", config={"swarm_concurrency_default": 9}) == 9


def test_probe_ramps_to_detect_a_big_parallel_server():
    # a fully-batched server keeps every burst parallel → the ramp climbs past the old cap,
    # so an 8-GPU vLLM box reads BIG with zero config
    def parallel_req():
        time.sleep(0.05)

    p = capacity.probe_concurrency("http://big/v1", "m", max_probe=32,
                                   requester=parallel_req, cache=False)
    assert p >= 8   # ramped well past a small box (loose bound: timing-based, robust under load)


def test_probe_detects_a_small_serial_server():
    # a lock forces one-at-a-time → the ramp stops immediately at ~1 (auto tone-down)
    lock = threading.Lock()

    def serial_req():
        with lock:
            time.sleep(0.02)

    s = capacity.probe_concurrency("http://small/v1", "m", max_probe=32,
                                   requester=serial_req, cache=False)
    assert s <= 3


def test_probe_result_is_cached_per_server():
    calls = {"n": 0}

    def req():
        calls["n"] += 1
        time.sleep(0.005)

    capacity.clear_probe_cache()
    a = capacity.probe_concurrency("http://cache/v1", "m", max_probe=8, requester=req)
    n_after_first = calls["n"]
    assert n_after_first > 1                       # it actually probed (warm + bursts)
    b = capacity.probe_concurrency("http://cache/v1", "m", max_probe=8, requester=req)
    assert a == b and calls["n"] == n_after_first  # cached: no new requests
