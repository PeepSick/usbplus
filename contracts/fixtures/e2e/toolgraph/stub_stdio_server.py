#!/usr/bin/env python3
"""Offline stdio MCP stub for mcprobe conformance (no SDK needed).

STUB_MODE:
  normal      3 tools, single page, complete
  cursorloop  distinct tools but the SAME nextCursor forever
              -> repeated-cursor stop
  badjson     tools/list answer is invalid JSON once -> protocol_error

Env MARKER_FILE: written on startup so tests can PROVE a process was or
was not launched.
"""
import json
import os
import sys

marker = os.environ.get("MARKER_FILE")
if marker:
    open(marker, "w", encoding="utf-8").close()

mode = os.environ.get("STUB_MODE", "normal")
TOOLS = [{"name": f"stub_tool_{i}", "description": f"stub tool {i}",
          "inputSchema": {"type": "object",
                          "properties": {"path": {"type": "string"}}}}
         for i in range(1, 4)]

out = sys.stdout


def send(obj):
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stub", "version": "0.1.0"}}})
    elif method == "tools/list":
        if mode == "badjson":
            out.write('{"jsonrpc":"2.0","id":' + str(msg.get("id")) +
                      ',"result":{"tools": [BROKEN}\n')
            out.flush()
            continue
        cursor = None
        if mode == "cursorloop":
            cursor = "LOOP"
            page = TOOLS[:2]
        else:
            page = TOOLS
        send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {
            "tools": page, **({"nextCursor": cursor} if cursor else {})}})
