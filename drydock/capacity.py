"""Inference-server capacity detection for swarm auto-sizing.

The swarm's useful agent count is bounded by how many requests the server actually serves
IN PARALLEL (§21/§22): the agents share one server, so past its real concurrency, extra
agents just queue — more tokens, no more wall-clock. So "hammer the 8-GPU vLLM box, tone
down the -np 2 laptop" reduces to: estimate the server's concurrency C, then size the swarm
to min(C, task-demand, budget). This module estimates C.

Detection is FULLY AUTOMATIC — the user configures nothing. Order (first hit wins), all
swallow-error / never-raise:
  1. llama.cpp — GET /slots (its `-np` parallel slots), a free exact read;
  2. empirical ramp probe — double a burst of tiny concurrent requests until the server
     saturates (server-agnostic; the honest signal for vLLM, whose max_num_seqs isn't exposed
     over the OpenAI API — an 8-GPU box reads big, a -np 2 laptop reads small, with no config);
  3. fallback — a conservative default.
An OPTIONAL escape hatch, checked first and needed by no one: `config['swarm_concurrency']`
or $DRYDOCK_SWARM_CONCURRENCY, for an operator who wants to pin it or skip probing entirely.

Stdlib-only; the probe funcs are injectable so this is testable without a live server.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import time
import urllib.request
from collections.abc import Callable

DEFAULT_CONCURRENCY = 4      # conservative when nothing else is known
DEFAULT_TASK_DEMAND = 6      # distinct approaches worth trying before agents just duplicate
HARD_MAX = 64                # never spawn more than this regardless of hardware


def _server_root(base_url: str) -> str:
    """Strip a trailing /v1 so we can hit sibling paths like /slots."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    return root


def _get_json(url: str, timeout: float = 4.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — operator's own server
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — detection is best-effort, never raises
        return None


def slots_concurrency(base_url: str, *, getter: Callable[[str], object] | None = None) -> int | None:
    """llama.cpp exposes its `-np` parallel slots at /slots (a list). len = concurrency."""
    get = getter or (lambda u: _get_json(u))
    d = get(_server_root(base_url) + "/slots")
    if isinstance(d, list) and d:
        return len(d)
    return None


def _default_requester(base_url: str, model: str) -> Callable[[], None]:
    def req() -> None:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1, "temperature": 0,
        }).encode("utf-8")
        r = urllib.request.Request(  # noqa: S310 — operator's own server
            base_url.rstrip("/") + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=60) as resp:
            resp.read()
    return req


_PROBE_CACHE: dict[str, int] = {}


def clear_probe_cache() -> None:
    """Forget cached probe results (mainly for tests / a changed server)."""
    _PROBE_CACHE.clear()


def _safe_call(req: Callable[[], None]) -> None:
    try:
        req()
    except Exception:  # noqa: BLE001
        pass


def _burst_wall(req: Callable[[], None], b: int) -> float:
    start = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=b) as ex:
            list(ex.map(lambda _: _safe_call(req), range(b)))
    except Exception:  # noqa: BLE001
        return -1.0
    return time.monotonic() - start


def probe_concurrency(base_url: str, model: str, *, max_probe: int = 64,
                      requester: Callable[[], None] | None = None, cache: bool = True) -> int:
    """Empirically estimate useful parallelism with ZERO configuration, by RAMPING the burst
    until the server saturates — so an 8-GPU vLLM box is detected as big and a -np 2 laptop
    as small, without anyone declaring anything.

    One warm request costs t1; a burst of B concurrent requests finishes in wall W, so
    parallelism P ≈ B·t1/W (≈B if fully batched, ≈1 if serialized). Ramp B = 4, 8, 16, …:
    while the server keeps the whole burst parallel (P ≈ B) it can take more, so double B;
    the first time it can't (P < B) is its ceiling. Cached per server (the probe costs a
    handful of max_tokens=1 requests, run once)."""
    if cache and base_url in _PROBE_CACHE:
        return _PROBE_CACHE[base_url]
    req = requester or _default_requester(base_url, model)
    try:
        t0 = time.monotonic()
        req()
        t1 = time.monotonic() - t0
    except Exception:  # noqa: BLE001 — server unreachable
        return 1
    if t1 <= 0:
        result = max_probe
    else:
        best, burst = 1, 4
        while burst <= max_probe:
            wall = _burst_wall(req, burst)
            p = burst if wall <= 0 else max(1, min(burst, round(burst * t1 / wall)))
            best = max(best, p)
            if p < burst * 0.75:          # couldn't keep the whole burst parallel → ceiling
                break
            burst *= 2
        result = best
    if cache:
        _PROBE_CACHE[base_url] = result
    return result


def detect_concurrency(base_url: str, *, provider: str = "vllm", model: str = "",
                       config: dict | None = None, probe: bool = True) -> int:
    """Estimate the server's useful concurrency C (see module docstring for the order)."""
    config = config or {}
    override = config.get("swarm_concurrency") or os.environ.get("DRYDOCK_SWARM_CONCURRENCY")
    if override:
        try:
            return max(1, int(override))
        except (TypeError, ValueError):
            pass
    slots = slots_concurrency(base_url)
    if slots:
        return slots
    if probe and model:
        c = probe_concurrency(base_url, model)
        if c:
            return c
    return int(config.get("swarm_concurrency_default", DEFAULT_CONCURRENCY))


def swarm_size(concurrency: int, *, task_demand: int = DEFAULT_TASK_DEMAND,
               budget_agents: int | None = None, hard_max: int = HARD_MAX) -> int:
    """N = min(hardware concurrency, task-demand, budget) — never maximized (§39: more
    agents ≠ more intelligence). On an 8-GPU box concurrency is high so task-demand binds
    (no wasteful cloning); on a small box concurrency binds (auto tone-down)."""
    n = min(max(1, concurrency), max(1, task_demand), hard_max)
    if budget_agents:
        n = min(n, max(1, budget_agents))
    return max(1, n)
