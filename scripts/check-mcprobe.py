#!/usr/bin/env python3
"""mcprobe conformance: consent contract + probe behaviours (offline stub).

Locks docs/mcprobe.md section 8:
  - no --yes with piped stdin -> refusal, NOTHING is launched
  - normal probe -> complete report, redaction enforced
  - repeated cursor -> partial observation, pagination_limit
  - malformed page -> protocol_error, exit 1
  - relative args -> child runs in the CONFIG dir (spawn cwd contract)

Run: python3 scripts/check-mcprobe.py
"""

import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROBE = ROOT / "packages/cli/bin/usb-mcprobe.py"
STUB = ROOT / "contracts/fixtures/e2e/toolgraph/stub_stdio_server.py"

failures = []


def check(name, ok, detail=""):
    print(f"-- {name}\n{'OK' if ok else 'FAILED: ' + str(detail)}")
    if not ok:
        failures.append(name)


def write_config(root: Path, mode: str, marker: Path | None,
                 secret: bool = False):
    entry = {
        "command": sys.executable.replace("\\", "/"),
        "args": [posix(STUB)],
    }
    env = {"STUB_MODE": mode}
    if marker:
        env["MARKER_FILE"] = posix(marker)
    if secret:
        env["SECRET_TOKEN"] = "sk-abcdef1234567890secret"
        entry["args"] = entry["args"] + ["--token=sk-abcdef1234567890secret"]
    root.mkdir(parents=True, exist_ok=True)
    (root / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"stub": {**entry, "env": env}}}),
        encoding="utf-8", newline="\n")


def write_remote_config(root: Path, url: str, type_: str,
                        headers: dict):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"remote": {"type": type_, "url": url,
                                   "headers": headers}}}),
        encoding="utf-8", newline="\n")


def run(args, stdin_text=None, timeout=90):
    r = subprocess.run([sys.executable, str(PROBE), *args],
                       input=stdin_text, capture_output=True, text=True,
                       encoding="utf-8",
                       timeout=timeout)
    return r


def parse_report(stdout: str):
    start = stdout.find("{")
    return json.loads(stdout[start:]) if start >= 0 else None


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="usb-mcprobe-check-"))
    try:
        # A) consent: piped stdin without --yes must refuse and NOT launch
        rootA = tmp / "consent"
        markerA = tmp / "markerA.txt"
        write_config(rootA, "normal", markerA)
        r = run(["--json", str(rootA)], stdin_text="")
        refused = r.returncode == 2 and "refused" in (r.stdout + r.stderr)
        launched = markerA.exists()
        check("consent: piped stdin refuses, nothing launches",
              refused and not launched,
              f"rc={r.returncode} launched={launched}")

        # B) --yes runs; complete report; secret redacted everywhere
        rootB = tmp / "normal"
        write_config(rootB, "normal", None, secret=True)
        r = run(["--yes", "--json", str(rootB)])
        rep = parse_report(r.stdout)
        ok = rep is not None and r.returncode == 0 \
            and rep["summary"]["probed"] == 1
        unit = rep["reports"][0] if rep else {}
        ok = ok and unit.get("observation", {}) == {"complete": True,
                                                    "reason": None} \
            and [t["name"] for t in unit["tools"]] == [
                "stub_tool_1", "stub_tool_2", "stub_tool_3"]
        blob = json.dumps(rep)
        ok = ok and "sk-abcdef1234567890secret" not in blob \
            and "${SECRET_TOKEN}" in blob
        ok = ok and rep["deterministic"] is False \
            and rep["schema"] == "usb-mcprobe-report/v1"
        check("probe: handshake+tools_list ok, sorted, redacted, "
              "non-deterministic labeled", bool(ok),
              r.stdout[:400])

        # C) repeated cursor -> partial observation, pagination_limit
        rootC = tmp / "cursorloop"
        write_config(rootC, "cursorloop", None)
        r = run(["--yes", "--json", str(rootC)])
        rep = parse_report(r.stdout)
        unit = rep["reports"][0] if rep else {}
        obs = unit.get("observation", {})
        check("pagination: repeated cursor stops, partial marked",
              r.returncode == 0 and obs.get("complete") is False
              and obs.get("reason") == "pagination_limit"
              and len(unit.get("tools", [])) > 0,
              f"obs={obs} rc={r.returncode}")

        # D) malformed page -> protocol_error and exit 1
        rootD = tmp / "badjson"
        write_config(rootD, "badjson", None)
        r = run(["--yes", "--json", str(rootD)])
        rep = parse_report(r.stdout)
        unit = rep["reports"][0] if rep else {}
        hs = unit.get("handshake", {})
        hs_blob = json.dumps(hs).lower()
        check("protocol error: recorded, exit 1",
              r.returncode == 1 and "protocol_error" in hs_blob
              and unit.get("observation", {}).get("reason")
              == "protocol_error",
              f"rc={r.returncode} hs={hs}")

        # D2) cwd contract: the child runs in the CONFIG dir, so its own
        #     relative args resolve (docs/mcprobe.md: spawn(command, args,
        #     env) # cwd: config dir). Locks the fix: before it the child
        #     spawned in mcprobe's cwd, missed server.py and died on the
        #     handshake. The stub asserts its cwd against a config-declared
        #     env var, so cwd=config dir is locked exactly - not "works".
        rootD2 = tmp / "relcwd"
        rootD2.mkdir(parents=True)
        stub_py = posix(STUB)
        (rootD2 / "server.py").write_text(
            "import os, sys, runpy\n"
            "assert os.path.basename(os.getcwd()) == "
            "os.environ['USB_EXPECT_CWD'], 'cwd=' + os.getcwd()\n"
            "sys.argv = [" + json.dumps(stub_py) + "]\n"
            "runpy.run_path(" + json.dumps(stub_py) +
            ", run_name='__main__')\n",
            encoding="utf-8", newline="\n")
        (rootD2 / ".mcp.json").write_text(json.dumps(
            {"mcpServers": {"relcwd": {
                "command": sys.executable.replace("\\", "/"),
                "args": ["server.py"],
                "env": {"STUB_MODE": "normal",
                        "USB_EXPECT_CWD": rootD2.name}}}}),
            encoding="utf-8", newline="\n")
        r = run(["--yes", "--json", str(rootD2)])
        rep = parse_report(r.stdout)
        unit = rep["reports"][0] if rep else {}
        hs = unit.get("handshake", {})
        names = [t["name"] for t in unit.get("tools", [])]
        check("cwd contract: relative args resolve in the config dir",
              r.returncode == 0 and hs.get("initialize") == "ok"
              and hs.get("tools_list") == "ok"
              and unit.get("observation", {}).get("complete") is True
              and names == ["stub_tool_1", "stub_tool_2", "stub_tool_3"],
              f"rc={r.returncode} hs={hs} tools={names}")

        # ---- remote transport (streamable HTTP + SSE + auth) ----------
        state = {"session": True, "last_session_echo": None}

        def make_handler():
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def _send_json(self, code, obj, extra=None):
                    body = json.dumps(obj).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_DELETE(self):
                    self._send_json(200, {})

                def do_POST(self):
                    length = int(self.headers.get("Content-Length", 0))
                    try:
                        msg = json.loads(
                            self.rfile.read(length).decode("utf-8"))
                    except ValueError:
                        self._send_json(400, {"error": "bad"})
                        return
                    if state["session"]:
                        state["last_session_echo"] = \
                            self.headers.get("Mcp-Session-Id")
                    if make_handler.auth:
                        err = {"jsonrpc": "2.0", "id": msg.get("id"),
                               "error": {"code": -32000,
                                         "message": "unauthorized"}}
                        self._send_json(401, err)
                        return
                    if make_handler.bot_block:
                        err = {"jsonrpc": "2.0", "id": msg.get("id"),
                               "error": {"code": -32000,
                                         "message": "forbidden"}}
                        self._send_json(403, err)
                        return
                    method = msg.get("method")
                    payload_id = msg.get("id")
                    if method == "initialize":
                        payload = {"jsonrpc": "2.0", "id": payload_id,
                                   "result": {
                                       "protocolVersion": "2024-11-05",
                                       "capabilities": {"tools": {}},
                                       "serverInfo": {
                                           "name": "remote-stub",
                                           "version": "2.0"}}}
                        extra = {"Mcp-Session-Id": "sess-123"} \
                            if state["session"] else None
                        if make_handler.sse:
                            frame = "data: " + json.dumps(payload) + "\n\n"
                            bodyb = frame.encode()
                            self.send_response(200)
                            self.send_header("Content-Type",
                                             "text/event-stream")
                            for k, v in (extra or {}).items():
                                self.send_header(k, v)
                            self.send_header("Content-Length",
                                             str(len(bodyb)))
                            self.end_headers()
                            self.wfile.write(bodyb)
                        else:
                            self._send_json(200, payload, extra)
                    elif method == "tools/list":
                        tools = [{"name": f"remote_tool_{i}",
                                  "description": f"remote tool {i}",
                                  "inputSchema": {"properties": {
                                      "path": {}}}}
                                 for i in range(1, 4)]
                        payload = {"jsonrpc": "2.0", "id": payload_id,
                                   "result": {"tools": tools}}
                        if make_handler.sse:
                            frame = "data: " + json.dumps(payload) + "\n\n"
                            bodyb = frame.encode()
                            self.send_response(200)
                            self.send_header("Content-Type",
                                             "text/event-stream")
                            self.send_header("Content-Length",
                                             str(len(bodyb)))
                            self.end_headers()
                            self.wfile.write(bodyb)
                        else:
                            self._send_json(200, payload)
                    else:
                        self._send_json(200, {"jsonrpc": "2.0",
                                              "id": payload_id,
                                              "result": {}})

            return Handler

        handler_cls = make_handler()
        handler_cls.auth = False
        handler_cls.sse = False
        handler_cls.bot_block = False
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        # E1) streamable http: session echo + secret redaction + url strip
        make_handler.auth = False
        make_handler.sse = False
        make_handler.bot_block = False
        rootE = tmp / "remote"
        rootE.mkdir(parents=True)
        url = f"http://127.0.0.1:{port}/mcp?key=topsecret123"
        write_remote_config(rootE, url, "http",
                            {"Authorization": "Bearer sk-abc99987654321"})
        r = run(["--yes", "--json", str(rootE)])
        rep = parse_report(r.stdout)
        unit = rep["reports"][0] if rep else {}
        blob = json.dumps(rep)
        e1_ok = (
            r.returncode == 0
            and unit.get("transport") == "remote-http"
            and unit.get("contact") == "remote-network"
            and unit.get("session_id_present") is True
            and state["last_session_echo"] == "sess-123"
            and "topsecret123" not in blob
            and "sk-abc99987654321" not in blob
            and len(unit.get("tools", [])) == 3
            and unit["observation"]["complete"] is True)
        check("remote http: session echo, redaction, query stripped",
              bool(e1_ok),
              f"rc={r.returncode} unit={json.dumps(unit)[:300]}")

        # E2) auth failure -> honest auth_required, exit 1
        make_handler.auth = True
        make_handler.bot_block = False
        r = run(["--yes", "--json", str(rootE)])
        rep = parse_report(r.stdout)
        unit = rep["reports"][0] if rep else {}
        check("remote 401: auth_required, exit 1",
              r.returncode == 1
              and unit.get("observation", {}).get("reason")
              == "auth_required",
              f"rc={r.returncode} obs={unit.get('observation')}")

        # E2b) 403 without WWW-Authenticate -> bot_blocked, exit 1
        make_handler.auth = False
        make_handler.bot_block = True
        r = run(["--yes", "--json", str(rootE)])
        rep = parse_report(r.stdout)
        unit = rep["reports"][0] if rep else {}
        check("remote 403 no WWW-Auth: bot_blocked, exit 1",
              r.returncode == 1
              and unit.get("observation", {}).get("reason")
              == "bot_blocked",
              f"rc={r.returncode} obs={unit.get('observation')}")

        # E3) sse envelope parses identically
        make_handler.auth = False
        make_handler.bot_block = False
        make_handler.sse = True
        write_remote_config(rootE, url, "sse",
                            {"Authorization": "Bearer sk-abc99987654321"})
        r = run(["--yes", "--json", str(rootE)])
        rep = parse_report(r.stdout)
        unit = rep["reports"][0] if rep else {}
        check("remote sse: envelope parsed, complete report",
              r.returncode == 0
              and unit.get("transport") == "remote-sse"
              and len(unit.get("tools", [])) == 3
              and unit["observation"]["complete"] is True,
              f"rc={r.returncode} tools={unit.get('tools')}")
        server.shutdown()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"mcprobe checks FAILED ({len(failures)})")
        return 1
    print("mcprobe checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
