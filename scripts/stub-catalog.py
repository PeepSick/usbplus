#!/usr/bin/env python3
"""Stub USB catalog for offline CLI tests (scripts/check-integrity.sh).

Serves /api/install from pre-rendered fixture scripts (see
render-fixture-install.mjs) and /api/version. Prints the bound port on
stdout, then serves until killed. Never touches the real catalog.

Usage: python3 stub-catalog.py SCRIPT_DIR
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(sys.argv[1])


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/version":
            body = json.dumps({"current": "9.9.9"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/api/install":
            q = parse_qs(url.query)
            target = q.get("target", ["generic"])[0]
            slug = q.get("slug", [None])[0]
            name = f"install-{target}-{slug}.sh" if slug \
                else f"install-{target}-full.sh"
            path = SCRIPT_DIR / name
            if not path.is_file():
                self.send_response(404)
                self.end_headers()
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/x-shellscript")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()