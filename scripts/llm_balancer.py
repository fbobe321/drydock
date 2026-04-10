#!/usr/bin/env python3
"""Simple round-robin load balancer for two vLLM servers.
Listens on port 8001, forwards to localhost:8000 and 192.168.50.21:8000.
"""
import http.server
import urllib.request
import threading
import sys

BACKENDS = [
    "http://localhost:8000",
    "http://192.168.50.21:8000",
]
counter = [0]
lock = threading.Lock()

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        with lock:
            idx = counter[0] % len(BACKENDS)
            counter[0] += 1
        backend = BACKENDS[idx]
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
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
        except Exception as e:
            # Failover: try other backend
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
            except Exception as e2:
                self.send_error(502, f"Both backends failed: {e}, {e2}")
    
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
        except Exception as e:
            self.send_error(502, str(e))
    
    def log_message(self, format, *args):
        pass  # Suppress logs

if __name__ == "__main__":
    port = 8001
    server = http.server.HTTPServer(("0.0.0.0", port), ProxyHandler)
    server.socket.settimeout(None)
    print(f"Load balancer on :{port} → {BACKENDS}")
    server.serve_forever()
