#!/usr/bin/env python3
"""Dogfood: scan USBv2 contracts/fixtures for skills."""
import json, subprocess, sys
from pathlib import Path

MCP = Path(__file__).resolve().parent.parent / "packages/cli/bin/usb-mcp.py"
ROOT = Path(__file__).resolve().parent.parent

init = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "dogfood", "version": "0.1.0"}},
})
notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})

# Scan contracts/fixtures which has real skill definitions
call = json.dumps({
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "usb_scan",
               "arguments": {"path": str(ROOT / "contracts/fixtures")}},
})

proc = subprocess.run(
    [sys.executable, str(MCP)],
    input=f"{init}\n{notif}\n{call}\n",
    capture_output=True, text=True, encoding="utf-8", timeout=90,
)

for line in proc.stdout.strip().splitlines():
    try:
        msg = json.loads(line)
        if msg.get("id") == 2:
            content = msg.get("result", {}).get("content", [])
            if content:
                data = json.loads(content[0]["text"])
                print("=== usb_scan dogfood: contracts/fixtures ===")
                skills = data.get("skills", [])
                print(f"Skills found: {len(skills)}")
                verdicts = data.get("summary", {}).get("verdicts", {})
                print(f"Verdicts: {verdicts}")
                for s in skills[:10]:
                    print(f"  - {s['name']} ({s['format']}): {s['verdict']}")
                if len(skills) > 10:
                    print(f"  ... and {len(skills) - 10} more")
            break
    except Exception as e:
        print(f"parse error: {e}")
