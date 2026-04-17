#!/usr/bin/env python3
"""Read-only HTTP probe that exposes Admiral telemetry as JSON.

Runs on the drydock host; the dashboard box (192.168.50.21) fetches
`/api/admiral` from this endpoint. No writes, no mutating endpoints —
the worst case is information disclosure, so bind carefully.

Usage:
    python3 scripts/admiral_probe.py                  # default 0.0.0.0:8878
    python3 scripts/admiral_probe.py --bind 127.0.0.1 # local only
    python3 scripts/admiral_probe.py --port 9000
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger("admiral_probe")

HISTORY_LOG = Path.home() / ".drydock" / "logs" / "admiral_history.log"
METRICS_LOG = Path.home() / ".drydock" / "logs" / "admiral_metrics.jsonl"
TUNING_JSON = Path.home() / ".drydock" / "admiral_tuning.json"


def _tail(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    with path.open() as f:
        return [line.rstrip("\n") for line in f.readlines()[-n:]]


def _parse_history_line(line: str) -> dict | None:
    # format: "<iso-ts> | <event> | <detail>"
    parts = line.split(" | ", 2)
    if len(parts) != 3:
        return None
    ts, event, detail = parts
    return {"ts": ts, "event": event, "detail": detail}


def _installed_version() -> str | None:
    try:
        import importlib.metadata
        return importlib.metadata.version("drydock-cli")
    except Exception:
        return None


def _running_drydock_pids() -> list[int]:
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "drydock"], text=True, timeout=2
        )
        pids = []
        for line in out.splitlines():
            try:
                pids.append(int(line.strip()))
            except ValueError:
                pass
        # Filter out our own probe PID.
        return [p for p in pids if p != os.getpid()]
    except Exception:
        return []


def _snapshot() -> dict:
    raw_lines = _tail(HISTORY_LOG, 200)
    entries = [e for e in (_parse_history_line(l) for l in raw_lines) if e]
    # Per-directive-source breakdown (from Phase 2 wiring).
    source_counts: Counter[str] = Counter()
    for e in entries:
        if e["event"] == "directive-source":
            # detail looks like "code :: source=<name>"
            bits = e["detail"].rsplit("source=", 1)
            if len(bits) == 2:
                source_counts[bits[1].strip()] += 1
    # Interventions vs errors.
    event_counts = Counter(e["event"] for e in entries)
    # Tuning state.
    tuning: dict | str = {}
    if TUNING_JSON.exists():
        try:
            tuning = json.loads(TUNING_JSON.read_text())
        except Exception as e:
            tuning = f"<malformed: {e}>"
    # Metrics — recent session lines.
    metric_lines = _tail(METRICS_LOG, 50)
    metrics: list[dict] = []
    for line in metric_lines:
        try:
            metrics.append(json.loads(line))
        except Exception:
            pass
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "drydock_version": _installed_version(),
        "running_drydock_pids": _running_drydock_pids(),
        "history_tail": entries[-50:],
        "event_counts": dict(event_counts),
        "directive_source_counts": dict(source_counts),
        "tuning": tuning,
        "recent_metrics": metrics[-10:],
    }


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        return  # quiet

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET")
        self.send_header("Cache-Control", "no-store")

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/api/admiral", "/api/admiral/"):
            try:
                payload = json.dumps(_snapshot(), default=str).encode()
            except Exception as e:
                payload = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path in ("/healthz", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self._cors_headers()
        self.end_headers()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8878)
    args = p.parse_args()
    srv = ThreadingHTTPServer((args.bind, args.port), _Handler)
    print(f"admiral_probe serving http://{args.bind}:{args.port}/api/admiral")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
