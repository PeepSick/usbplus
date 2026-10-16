#!/usr/bin/env python3
"""usb mcp - MCP stdio server exposing USB scan and mcprobe as tools.

Thin transport layer over existing CLI tools. No new scanner logic,
no new probe logic, no new capability taxonomy.

Tools:
  usb_scan      - deterministic skill contract scan (wraps usb secscan --json)
  usb_mcprobe   - runtime MCP server observation (wraps usb mcprobe --json)
                  REQUIRES consent=true; MCP transport is NOT a bypass.

Usage:
  python3 usb-mcp.py                  # stdio MCP server
  python3 usb-mcp.py --help           # show usage

Exit codes: 0 clean, 1 error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

TOOL = "usb-mcp"
VERSION = "0.1.0"
SECSCAN = Path(__file__).resolve().parent / "usb-secscan.py"
MCPROBE = Path(__file__).resolve().parent / "usb-mcprobe.py"

server = Server(TOOL, version=VERSION)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

USB_SCAN_TOOL = types.Tool(
    name="usb_scan",
    description=(
        "Deterministic skill-contract security scan. Wraps `usb secscan --json`. "
        "Scans a path or repository for agent skill definitions, checks declared "
        "vs observed capabilities, and returns structured findings with verdicts."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Local directory path to scan",
            },
            "baseline": {
                "type": "string",
                "description": "Optional path to baseline file; only new findings gate exit code",
            },
        },
        "required": ["path"],
    },
)

USB_MCPROBE_TOOL = types.Tool(
    name="usb_mcprobe",
    description=(
        "Runtime observation of an MCP server. Wraps `usb mcprobe --json`. "
        "Contacts the target, performs initialize/tools_list handshake, "
        "records exposed tools, disconnects. NON-deterministic by design. "
        "REQUIRES consent=true - MCP transport is NOT a consent bypass."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": (
                    "Path to scan root or MCP config file containing mcpServers. "
                    "Same as the positional arg to `usb mcprobe`."
                ),
            },
            "consent": {
                "type": "boolean",
                "description": (
                    "Must be true to execute. When false, returns a dry-run plan "
                    "without contacting any target."
                ),
            },
            "timeout": {
                "type": "number",
                "description": "Per-phase timeout in seconds (default: 30)",
            },
        },
        "required": ["target", "consent"],
    },
)


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run_secscan(path: str, baseline: str | None = None) -> dict:
    """Run usb secscan --json and return parsed result."""
    cmd = [sys.executable, str(SECSCAN), "--json", path]
    if baseline:
        cmd.extend(["--baseline", baseline])

    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )

    stdout = result.stdout.strip()
    if not stdout:
        return {
            "error": "secscan produced no output",
            "exit_code": result.returncode,
            "stderr": result.stderr[:2000] if result.stderr else None,
        }

    try:
        # secscan may print human lines before JSON; find the JSON object
        json_start = stdout.find("{")
        if json_start < 0:
            return {
                "error": "no JSON in secscan output",
                "exit_code": result.returncode,
                "raw_output": stdout[:2000],
            }
        return json.loads(stdout[json_start:])
    except json.JSONDecodeError as exc:
        return {
            "error": f"invalid JSON from secscan: {exc}",
            "exit_code": result.returncode,
            "raw_output": stdout[:2000],
        }


def _run_mcprobe(target: str, consent: bool, timeout: float | None = None) -> dict:
    """Run usb mcprobe --json --yes and return parsed result."""
    if not consent:
        return {
            "error": "consent required",
            "detail": (
                "usb_mcprobe requires consent=true. MCP transport is not "
                "a consent bypass. Set consent=true to execute."
            ),
        }

    cmd = [sys.executable, str(MCPROBE), "--yes", "--json", target]
    if timeout is not None:
        cmd.extend(["--timeout", str(timeout)])

    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )

    stdout = result.stdout.strip()
    if not stdout:
        return {
            "error": "mcprobe produced no output",
            "exit_code": result.returncode,
            "stderr": result.stderr[:2000] if result.stderr else None,
        }

    try:
        json_start = stdout.find("{")
        if json_start < 0:
            return {
                "error": "no JSON in mcprobe output",
                "exit_code": result.returncode,
                "raw_output": stdout[:2000],
            }
        return json.loads(stdout[json_start:])
    except json.JSONDecodeError as exc:
        return {
            "error": f"invalid JSON from mcprobe: {exc}",
            "exit_code": result.returncode,
            "raw_output": stdout[:2000],
        }


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [USB_SCAN_TOOL, USB_MCPROBE_TOOL]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict,
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if name == "usb_scan":
        path = arguments.get("path")
        if not path:
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": "path is required"}),
            )]
        baseline = arguments.get("baseline")
        result = _run_secscan(path, baseline)
        return [types.TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, indent=2),
        )]

    if name == "usb_mcprobe":
        target = arguments.get("target")
        consent = arguments.get("consent", False)
        if not target:
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": "target is required"}),
            )]
        timeout = arguments.get("timeout")
        result = _run_mcprobe(target, consent, timeout)
        return [types.TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, indent=2),
        )]

    return [types.TextContent(
        type="text",
        text=json.dumps({"error": f"unknown tool: {name}"}),
    )]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main():
    parser = argparse.ArgumentParser(
        prog="usb mcp",
        description="USB MCP server - stdio transport for AI agents",
    )
    parser.parse_args()  # no args needed for stdio mode
    asyncio.run(_main())


if __name__ == "__main__":
    main()
