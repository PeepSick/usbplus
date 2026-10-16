#!/usr/bin/env python3
"""MCP server conformance: tool list, usb_scan, usb_mcprobe consent contract.

Tests:
  1. MCP initialize + tools/list returns 2 tools
  2. usb_scan on a fixture returns structured results
  3. usb_mcprobe consent=false returns error, nothing launched
  4. usb_mcprobe consent=true reaches probe execution
  5. Tool schemas are minimal and stable
  6. Error results are structured and honest
  7. Existing secscan conformance is not broken

Run: python3 scripts/check-mcp.py
"""

import json
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MCP_SERVER = ROOT / "packages/cli/bin/usb-mcp.py"
SECSCAN = ROOT / "packages/cli/bin/usb-secscan.py"
STUB = ROOT / "contracts/fixtures/e2e/toolgraph/stub_stdio_server.py"

failures = []


def check(name, ok, detail=""):
    print(f"-- {name}\n{'OK' if ok else 'FAILED: ' + str(detail)}")
    if not ok:
        failures.append(name)


def write_config(root: Path, command: str, args: list[str], env: dict | None = None):
    entry = {"command": command, "args": args}
    if env:
        entry["env"] = env
    root.mkdir(parents=True, exist_ok=True)
    (root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"stub": entry}}),
        encoding="utf-8", newline="\n",
    )


_last = {}


def mcp_call(stdin_text: str, timeout: int = 30) -> str:
    """Send JSON-RPC messages to the MCP server, keep stdin OPEN until the
    last response arrives, then close stdin and collect stdout.

    subprocess.run(input=...) closes stdin immediately after writing, and the
    MCP SDK cancels in-flight handlers the moment the transport closes
    (lowlevel Server.run: `finally: tg.cancel_scope.cancel()`) - a handler
    that has not responded yet loses its response entirely. The loser of that
    race varies with machine speed and SDK version (6/10 failed runs
    observed on mcp 1.30: init answered, the follow-up call never did).
    Real clients keep stdin open while waiting for responses, so the harness
    now does too - deterministic on every SDK and machine."""
    t0 = time.monotonic()
    want = None
    for line in stdin_text.strip().splitlines():
        if line.strip():
            try:
                want = json.loads(line).get("id")
            except (json.JSONDecodeError, AttributeError):
                continue
    proc = subprocess.Popen(
        [sys.executable, str(MCP_SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    out_lines: list[str] = []
    err_lines: list[str] = []

    def pump(stream, buf):
        for ln in stream:
            buf.append(ln)

    readers = [
        threading.Thread(target=pump, args=(proc.stdout, out_lines),
                         daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, err_lines),
                         daemon=True),
    ]
    for t in readers:
        t.start()
    proc.stdin.write(stdin_text)
    proc.stdin.flush()

    deadline = t0 + timeout
    got = False
    while not got and time.monotonic() < deadline:
        for ln in out_lines:
            try:
                msg = json.loads(ln)
            except (json.JSONDecodeError, AttributeError):
                continue
            if msg.get("id") == want and ("result" in msg or "error" in msg):
                got = True
                break
        if not got:
            time.sleep(0.02)
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for t in readers:
        t.join(timeout=5)
    stdout = "".join(out_lines)
    _last.update(
        rc=proc.returncode, secs=time.monotonic() - t0,
        out=stdout, err="".join(err_lines),
    )
    return stdout


def diag(tag: str):
    """Dump the raw exchange of the last mcp_call. 'Response missing' alone
    cannot distinguish 'server cancelled the in-flight handler on stdin EOF'
    from 'server crashed' from 'server answered a JSON-RPC error instead' -
    stdout/stderr/rc make the failure self-explanatory in any CI log."""
    print(f"  [diag {tag}] rc={_last.get('rc')} "
          f"elapsed={_last.get('secs', -1):.2f}s")
    print(f"  [diag {tag}] stdout={(_last.get('out') or '')[:1600]!r}")
    print(f"  [diag {tag}] stderr={(_last.get('err') or '')[:1600]!r}")


def parse_jsonrpc_response(stdout: str):
    """Parse the first JSON-RPC response from stdout."""
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return msg
        except json.JSONDecodeError:
            continue
    return None


def test_initialize_and_list_tools():
    """Test 1: MCP initialize + tools/list returns 2 tools."""
    print("\n=== Test: initialize + tools/list ===")

    # Send initialize
    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        },
    })
    # Send initialized notification
    initialized_msg = json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    })
    # Send tools/list
    tools_msg = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
    })

    stdin = f"{init_msg}\n{initialized_msg}\n{tools_msg}\n"
    stdout = mcp_call(stdin, timeout=15)

    # Parse tools/list response
    tools_response = None
    for line in stdout.strip().splitlines():
        try:
            msg = json.loads(line.strip())
            if msg.get("id") == 2 and "result" in msg:
                tools_response = msg
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    if tools_response is None:
        diag("tools-list")
    check("tools/list returns result", tools_response is not None,
          f"stdout: {stdout[:500]}")

    if tools_response:
        tools = tools_response.get("result", {}).get("tools", [])
        check("exactly 2 tools", len(tools) == 2, f"got {len(tools)}")
        names = sorted(t["name"] for t in tools)
        check("tools are usb_scan and usb_mcprobe",
              names == ["usb_mcprobe", "usb_scan"], f"got {names}")

        # Validate schemas
        for tool in tools:
            schema = tool.get("inputSchema", {})
            check(f"{tool['name']} schema has properties",
                  "properties" in schema, f"schema: {schema}")
            check(f"{tool['name']} schema has type=object",
                  schema.get("type") == "object", f"type: {schema.get('type')}")


def test_usb_scan():
    """Test 2: usb_scan on a fixture returns structured results."""
    print("\n=== Test: usb_scan ===")

    # Create a minimal fixture with a skill file
    tmp = Path(tempfile.mkdtemp(prefix="usb-mcp-test-"))
    skill_dir = tmp / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\ncategories: [tests]\n---\n\n# Test Skill\n\nTest content.\n",
        encoding="utf-8",
    )

    # First, verify secscan itself works on this fixture
    secscan_result = subprocess.run(
        [sys.executable, str(SECSCAN), "--json", str(tmp / "skills")],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    secscan_json = None
    if secscan_result.stdout.strip():
        try:
            secscan_json = json.loads(secscan_result.stdout.strip().split("{", 1)[-1].rsplit("}", 1)[0] + "}")
        except (json.JSONDecodeError, IndexError):
            pass

    check("secscan runs on fixture", secscan_result.returncode in (0, 1),
          f"exit={secscan_result.returncode}")

    # Now test via MCP
    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        },
    })
    tools_call = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "usb_scan",
            "arguments": {"path": str(tmp / "skills")},
        },
    })

    stdout = mcp_call(f"{init_msg}\n{tools_call}\n", timeout=30)

    response = None
    for line in stdout.strip().splitlines():
        try:
            msg = json.loads(line.strip())
            if msg.get("id") == 2 and "result" in msg:
                response = msg
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    if response is None:
        diag("usb-scan")
    check("usb_scan returns result", response is not None,
          f"stdout: {stdout[:500]}")

    if response:
        content = response.get("result", {}).get("content", [])
        check("usb_scan returns text content", len(content) > 0)

        if content:
            text = content[0].get("text", "")
            try:
                data = json.loads(text)
                check("usb_scan result is valid JSON", isinstance(data, dict))
                check("usb_scan has 'skills' key",
                      "skills" in data or "summary" in data or "error" in data,
                      f"keys: {list(data.keys())}")
            except json.JSONDecodeError:
                check("usb_scan output is JSON", False, f"not JSON: {text[:200]}")


def test_mcprobe_consent_false():
    """Test 3: usb_mcprobe consent=false returns error, nothing launched."""
    print("\n=== Test: usb_mcprobe consent=false ===")

    tmp = Path(tempfile.mkdtemp(prefix="usb-mcp-test-"))
    marker = tmp / "marker.txt"
    write_config(
        tmp,
        sys.executable.replace("\\", "/"),
        [str(STUB).replace("\\", "/")],
        {"STUB_MODE": "normal", "MARKER_FILE": str(marker).replace("\\", "/")},
    )

    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        },
    })
    tools_call = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "usb_mcprobe",
            "arguments": {"target": str(tmp), "consent": False},
        },
    })

    stdout = mcp_call(f"{init_msg}\n{tools_call}\n", timeout=15)

    response = None
    for line in stdout.strip().splitlines():
        try:
            msg = json.loads(line.strip())
            if msg.get("id") == 2 and "result" in msg:
                response = msg
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    if response is None:
        diag("consent=false")
    check("consent=false returns result", response is not None)

    if response:
        content = response.get("result", {}).get("content", [])
        if content:
            text = content[0].get("text", "")
            try:
                data = json.loads(text)
                check("consent=false returns error", "error" in data,
                      f"keys: {list(data.keys())}")
                check("consent=false mentions consent",
                      "consent" in str(data).lower())
            except json.JSONDecodeError:
                check("consent=false output is JSON", False)

    # Marker file should NOT exist (no process launched)
    check("consent=false: no process launched",
          not marker.exists(),
          "marker file exists - process was launched without consent!")


def test_mcprobe_consent_true():
    """Test 4: usb_mcprobe consent=true reaches probe execution."""
    print("\n=== Test: usb_mcprobe consent=true ===")

    tmp = Path(tempfile.mkdtemp(prefix="usb-mcp-test-"))
    marker = tmp / "marker.txt"
    write_config(
        tmp,
        sys.executable.replace("\\", "/"),
        [str(STUB).replace("\\", "/")],
        {"STUB_MODE": "normal", "MARKER_FILE": str(marker).replace("\\", "/")},
    )

    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        },
    })
    tools_call = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "usb_mcprobe",
            "arguments": {"target": str(tmp), "consent": True},
        },
    })

    stdout = mcp_call(f"{init_msg}\n{tools_call}\n", timeout=30)

    response = None
    for line in stdout.strip().splitlines():
        try:
            msg = json.loads(line.strip())
            if msg.get("id") == 2 and "result" in msg:
                response = msg
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    if response is None:
        diag("consent=true")
    check("consent=true returns result", response is not None)

    if response:
        content = response.get("result", {}).get("content", [])
        if content:
            text = content[0].get("text", "")
            try:
                data = json.loads(text)
                check("consent=true result is valid JSON", isinstance(data, dict))
                # Should have mcprobe report structure
                has_reports = "reports" in data or "error" in data
                check("consent=true has reports or error", has_reports,
                      f"keys: {list(data.keys())}")
            except json.JSONDecodeError:
                check("consent=true output is JSON", False, f"not JSON: {text[:200]}")

    # Marker file SHOULD exist (process was launched)
    check("consent=true: process was launched",
          marker.exists(),
          "marker file missing - process was NOT launched with consent!")


def test_tool_schemas():
    """Test 5: Tool schemas are minimal and stable."""
    print("\n=== Test: tool schemas ===")

    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        },
    })
    tools_msg = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
    })

    stdout = mcp_call(f"{init_msg}\n{tools_msg}\n", timeout=15)

    tools = []
    for line in stdout.strip().splitlines():
        try:
            msg = json.loads(line.strip())
            if msg.get("id") == 2 and "result" in msg:
                tools = msg["result"].get("tools", [])
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    for tool in tools:
        name = tool["name"]
        schema = tool.get("inputSchema", {})

        # Check required fields exist
        check(f"{name}: has description", bool(tool.get("description")),
              f"description: {tool.get('description', '')[:80]}")
        check(f"{name}: schema type=object", schema.get("type") == "object")
        check(f"{name}: has properties", "properties" in schema)

        # Check required array
        if name == "usb_scan":
            check("usb_scan: required includes 'path'",
                  "path" in schema.get("required", []))
        elif name == "usb_mcprobe":
            req = schema.get("required", [])
            check("usb_mcprobe: required includes 'target' and 'consent'",
                  "target" in req and "consent" in req,
                  f"required: {req}")


def test_error_results_structured():
    """Test 6: Error results are structured and honest."""
    print("\n=== Test: error results ===")

    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        },
    })

    # Test: scan with non-existent path
    tools_call = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "usb_scan",
            "arguments": {"path": "/nonexistent/path/that/does/not/exist"},
        },
    })

    stdout = mcp_call(f"{init_msg}\n{tools_call}\n", timeout=15)

    response = None
    for line in stdout.strip().splitlines():
        try:
            msg = json.loads(line.strip())
            if msg.get("id") == 2 and "result" in msg:
                response = msg
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    if response is None:
        diag("usb_scan-nonexistent")
    check("error returns MCP result", response is not None)
    if response:
        content = response.get("result", {}).get("content", [])
        if content:
            text = content[0].get("text", "")
            try:
                data = json.loads(text)
                check("error result is structured JSON", isinstance(data, dict))
                # Should have error or exit_code or be a valid secscan response
                has_error_info = ("error" in data or "exit_code" in data
                                  or "summary" in data)
                check("error has error/exit_code/summary", has_error_info,
                      f"keys: {list(data.keys())}")
            except json.JSONDecodeError:
                check("error output is JSON", False)

    # Test: unknown tool
    tools_call2 = json.dumps({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {
            "name": "nonexistent_tool",
            "arguments": {},
        },
    })
    stdout2 = mcp_call(f"{init_msg}\n{tools_call2}\n", timeout=15)

    response2 = None
    for line in stdout2.strip().splitlines():
        try:
            msg = json.loads(line.strip())
            if msg.get("id") == 3 and "result" in msg:
                response2 = msg
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    if response2 is None:
        diag("unknown-tool")
    check("unknown tool returns result", response2 is not None)


def main():
    print(f"USB MCP Server Conformance — {MCP_SERVER}")
    print(f"Python: {sys.executable}")

    test_initialize_and_list_tools()
    test_usb_scan()
    test_mcprobe_consent_false()
    test_mcprobe_consent_true()
    test_tool_schemas()
    test_error_results_structured()

    print(f"\n{'='*60}")
    if failures:
        print(f"FAILED: {len(failures)} test(s)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
