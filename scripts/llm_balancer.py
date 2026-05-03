#!/usr/bin/env python3
"""Simple round-robin load balancer for two vLLM servers.
Listens on port 8001, forwards to localhost:8000 and 192.168.50.21:8000.

Failure handling:
- Per-request try/except around the entire handler body so a client
  disconnecting mid-response (BrokenPipeError, ConnectionResetError)
  doesn't crash the request thread or — worse — propagate upward in
  the server. BaseHTTPRequestHandler's default behavior emits a
  traceback to stderr and lets the exception bubble; under load this
  has been observed to cycle the whole process even though
  ThreadingHTTPServer is supposed to isolate threads.
- The cron keepalive (`*/5 * * * *` in crontab) was firing every
  ~30-60 min for this process before the fix went in.
"""
import http.server
import socket
import sys
import threading
import urllib.request

BACKENDS = [
    "http://localhost:8000",
    "http://192.168.50.21:8000",
]
counter = [0]
lock = threading.Lock()

# Errors we treat as "client gave up" — log + drop, never crash.
_CLIENT_DROP_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    socket.timeout,
)


def _safe_send_error(handler, code: int, message: str) -> None:
    """send_error itself can raise on a dead client. Don't let that
    propagate."""
    try:
        handler.send_error(code, message)
    except _CLIENT_DROP_ERRORS:
        pass
    except Exception as e:  # last resort — log, do not crash
        print(f"[balancer] send_error failed: {e}", file=sys.stderr, flush=True)


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def _forward_post(self, body: bytes) -> None:
        with lock:
            idx = counter[0] % len(BACKENDS)
            counter[0] += 1
        backend = BACKENDS[idx]
        url = f"{backend}{self.path}"
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('Content-Type', 'application/json')

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for key, val in resp.headers.items():
                    if key.lower() not in ('transfer-encoding', 'connection'):
                        self.send_header(key, val)
                self.end_headers()
                self.wfile.write(data)
                return
        except _CLIENT_DROP_ERRORS:
            return  # client gave up, no need to failover
        except Exception as e:
            primary_err = e

        # Failover: try the other backend.
        other = BACKENDS[1 - idx]
        url = f"{other}{self.path}"
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for key, val in resp.headers.items():
                    if key.lower() not in ('transfer-encoding', 'connection'):
                        self.send_header(key, val)
                self.end_headers()
                self.wfile.write(data)
        except _CLIENT_DROP_ERRORS:
            return
        except Exception as e2:
            _safe_send_error(self, 502, f"Both backends failed: {primary_err}, {e2}")

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            self._forward_post(body)
        except _CLIENT_DROP_ERRORS:
            return
        except Exception as e:  # last-resort guard for the whole request
            print(f"[balancer] do_POST crash: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            _safe_send_error(self, 500, str(e))

    def do_GET(self):
        backend = BACKENDS[0]
        url = f"{backend}{self.path}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)
        except _CLIENT_DROP_ERRORS:
            return
        except Exception as e:
            _safe_send_error(self, 502, str(e))

    def log_message(self, format, *args):
        pass  # Suppress access logs

    def log_error(self, format, *args):
        # Default impl writes tracebacks to stderr — keep that, but
        # collapse the noise when it's just a client-disconnect.
        msg = format % args if args else format
        if "Broken pipe" in msg or "Connection reset" in msg:
            return
        sys.stderr.write(f"[balancer] {msg}\n")
        sys.stderr.flush()


class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    """Each request handled in its own thread so long LLM calls don't block."""
    daemon_threads = True


if __name__ == "__main__":
    port = 8001
    server = ThreadedHTTPServer(("0.0.0.0", port), ProxyHandler)
    server.socket.settimeout(None)
    print(f"Load balancer on :{port} → {BACKENDS}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
