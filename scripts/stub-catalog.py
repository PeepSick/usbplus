#!/usr/bin/env python3
"""Stub USB catalog for offline CLI tests (scripts/check-integrity.sh).

Serves /api/install from pre-rendered fixture scripts (see
render-fixture-install.mjs) and /api/version. Prints the bound port on
stdout, then serves until killed. Never touches the real catalog.

Optionally serves /engines/<file> from ENGINES_DIR (signed engine bootstrap).

Usage: python3 stub-catalog.py SCRIPT_DIR [ENGINES_DIR]
"""
import json
import os
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(sys.argv[1])
ENGINES_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else None


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
            sigfile = Path(str(path) + ".sig")
            sig = sigfile.read_text().strip() if sigfile.is_file() else None
            if os.environ.get("STUB_TAMPER") == "1":
                body += b"\n# tampered in transit\n"  # signature no longer matches
            self.send_response(200)
            self.send_header("Content-Type", "text/x-shellscript")
            if sig:
                self.send_header("X-USB-Signature", sig)
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path.startswith("/engines/") and ENGINES_DIR is not None:
            name = url.path[len("/engines/"):]
            path = ENGINES_DIR / name
            if "/" not in name and path.is_file():
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(body)
                return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


class QuickServer(ThreadingHTTPServer):
    # http.server.HTTPServer.server_bind() resolves the bind address with
    # socket.getfqdn(), a reverse-DNS lookup that can hang for many seconds on
    # macOS CI runners - the port was never printed and the suite gave up.
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


server = QuickServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()