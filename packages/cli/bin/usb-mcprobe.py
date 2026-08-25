#!/usr/bin/env python3
"""usb mcprobe - runtime observation for MCP servers.

NON-DETERMINISTIC by design (docs/mcprobe.md): launches a configured MCP
server over stdio, performs the initialize/tools_list handshake, records
the exposed tools, shuts down. Never calls tools. Separate from secscan's
deterministic audit chain by construction.

Consent contract (docs/mcprobe.md section 8):
    no --yes          -> prints the plan and stops; interactive prompt only
                         on a real TTY; piped stdin counts as refusal
    --yes             -> explicit machine consent, runs immediately

Exit codes: 0 all probes contacted successfully, 1 at least one probe hit
a protocol error/timeout, 2 refusal / no targets / usage error.

Usage:
    usb-mcprobe.py [--yes] [--json] [--timeout SECS] [PATH]...
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

SCHEMA = "usb-mcprobe-report/v1"
PROTOCOL_VERSION = "2024-11-05"
PAGE_LIMIT = 10                    # docs/mcprobe.md section 8
TOOL_CEILING = 1000
PHASE_TIMEOUT_DEFAULT = 30.0
SHUTDOWN_GRACE = 3.0
MAX_LINE = 512 * 1024

ENV_ALLOWLIST = ("PATH", "HOME", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP",
                 "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "USERPROFILE")

SECRET_PATTERN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def _load_engine():
    path = Path(__file__).resolve().parent / "usb-secscan.py"
    spec = importlib.util.spec_from_file_location("usb_secscan_engine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENGINE = _load_engine()


def redact(text: str, secrets: list[tuple[str, str]]) -> str:
    out = text or ""
    for key, value in secrets:
        if value and len(value) > 3:
            out = out.replace(value, f"${{{key}}}")
    out = SECRET_PATTERN_RE.sub("<redacted>", out)
    return out


class ProbeError(Exception):
    def __init__(self, phase: str, message: str):
        super().__init__(message)
        self.phase = phase
        self.message = message


class LineReader(threading.Thread):
    """Background reader: the server's stdout becomes a line queue so the
    main thread can enforce per-phase deadlines."""

    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.lines: queue.Queue = queue.Queue()
        self.dead = False

    def run(self):
        try:
            for raw in self.stream:
                self.lines.put(raw.decode("utf-8", errors="replace")
                               [:MAX_LINE])
        except Exception:
            pass
        self.dead = True
        self.lines.put(None)

    def read_line(self, deadline: float) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("timeout", "deadline exceeded while waiting "
                                        "for a response")
        try:
            item = self.lines.get(timeout=remaining)
        except queue.Empty:
            raise ProbeError("timeout", "deadline exceeded while waiting "
                                        "for a response")
        if item is None:
            raise ProbeError("protocol_error",
                             "server closed stdout unexpectedly")
        return item

    def read_message(self, deadline: float, strict: bool = False):
        while True:
            line = self.read_line(deadline).strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                # Pre-handshake banners may be non-JSON; once we are inside
                # an exchange, an unparseable frame IS a protocol error.
                if strict:
                    raise ProbeError("protocol_error",
                                     "malformed JSON-RPC frame")
                continue
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return msg


def send(writer, payload: dict):
    writer.write((json.dumps(payload, ensure_ascii=False) + "\n")
                 .encode("utf-8"))
    writer.flush()


def discover_targets(roots: list[Path]):
    targets = []
    for root in roots:
        if not root.exists():
            continue
        cfgs = ENGINE.find_mcp_configs(root)
        for rel, cfg_path in cfgs:
            try:
                raw = cfg_path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            lines = (raw.decode("utf-8", errors="replace")
                     .lstrip("\ufeff").splitlines())
            servers, structural = ENGINE.parse_mcp_config(lines)
            if servers is None:
                continue
            display = posix(cfg_path)
            for name in sorted(servers):
                entry = servers[name]
                if isinstance(entry, dict):
                    targets.append({
                        "locator": f"{display}#mcpServers/{name}",
                        "name": name,
                        "entry": entry,
                        "anchor": ENGINE._anchor_line(lines, name),
                    })
    return targets


def posix(p) -> str:
    return str(p).replace("\\", "/")


def contact_class(entry: dict) -> str | None:
    if isinstance(entry.get("command"), str) and entry["command"].strip():
        return "local-process"
    if isinstance(entry.get("url"), str) and entry["url"].strip():
        return "remote"
    return None


def probe_stdio(target: dict, timeout: float) -> dict:
    entry = target["entry"]
    command = entry["command"].strip()
    raw_args = [a for a in entry.get("args") or [] if isinstance(a, str)]
    argv = [command] + raw_args

    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
    declared_env = entry.get("env") if isinstance(entry.get("env"), dict) \
        else {}
    env.update({str(k): str(v) for k, v in declared_env.items()})
    secrets = [(str(k), str(v)) for k, v in declared_env.items()]

    cwd = None
    for a in raw_args:
        candidate = Path.cwd().joinpath(a)
        if candidate.is_dir():
            cwd = str(candidate)
            break

    report = {
        "locator": target["locator"],
        "transport": "stdio",
        "contact": "local-process",
        "launch_line_redacted": redact(" ".join(argv), secrets),
        "handshake": {"initialize": "error", "tools_list": "skipped"},
        "server_info": {},
        "observation": {"complete": False, "reason": None},
        "tools": [],
    }
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, cwd=cwd)
    except OSError as exc:
        report["handshake"]["initialize"] = f"spawn_error: {exc}"
        return report

    reader = LineReader(proc.stdout)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        send(proc.stdin, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "usb-mcprobe", "version": "0.1.0"},
            }})
        init = reader.read_message(deadline)
        result = init.get("result") or {}
        info = result.get("serverInfo") or {}
        report["server_info"] = {
            "name": str(info.get("name", ""))[:120],
            "version": str(info.get("version", ""))[:40],
        }
        if "error" in init:
            raise ProbeError("initialize", "server returned JSON-RPC error")
        report["handshake"]["initialize"] = "ok"

        send(proc.stdin, {"jsonrpc": "2.0", "method": "notifications"
                          "/initialized"})
        report["handshake"]["initialize"] = "ok"

        cursor = None
        seen_cursors = set()
        pages = 0
        tools = []
        complete = True
        reason = None
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            send(proc.stdin, {"jsonrpc": "2.0", "id": 2 + pages,
                              "method": "tools/list", "params": params})
            page_deadline = min(deadline, time.monotonic() + timeout)
            resp = reader.read_message(page_deadline, strict=True)
            if "error" in resp:
                raise ProbeError("tools_list",
                                 "server returned JSON-RPC error")
            payload = resp.get("result") or {}
            page_tools = payload.get("tools")
            if not isinstance(page_tools, list):
                raise ProbeError("tools_list", "malformed tools page")
            pages += 1
            for t in page_tools[:max(0, TOOL_CEILING - len(tools))]:
                if not isinstance(t, dict) or not isinstance(t.get("name"),
                                                             str):
                    raise ProbeError("tools_list",
                                     "malformed tool entry")
                desc = t.get("description")
                tools.append({
                    "name": t["name"][:200],
                    "description": (redact(str(desc)[:300], secrets)
                                    if isinstance(desc, str) else ""),
                    "input_schema_keys": sorted(
                        k for k in ((t.get("inputSchema") or {})
                                    .get("properties") or {})
                        if isinstance(k, str))[:20],
                })
            cursor = payload.get("nextCursor")
            if len(tools) >= TOOL_CEILING:
                complete, reason = False, "pagination_limit"
                break
            if not cursor:
                break
            if cursor in seen_cursors or pages >= PAGE_LIMIT:
                complete, reason = False, "pagination_limit"
                break
            seen_cursors.add(cursor)

        report["observation"] = {"complete": complete, "reason": reason}
        report["tools"] = sorted(tools, key=lambda t: t["name"])
        report["handshake"]["tools_list"] = "ok"
    except ProbeError as exc:
        report["handshake"][exc.phase] = exc.message
        report["observation"] = {"complete": False, "reason": exc.phase}
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=SHUTDOWN_GRACE)
        except Exception:
            proc.kill()

    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="usb mcprobe",
        description="Runtime observation of MCP servers - launches the "
                    "target, lists its tools, shuts down. NON-deterministic "
                    "and opt-in by design.")
    parser.add_argument("targets", nargs="*",
                        help="config paths or scan roots (same discovery as "
                             "secscan)")
    parser.add_argument("--yes", action="store_true",
                        help="explicit machine consent to launch targets")
    parser.add_argument("--json", action="store_true", default=True)
    parser.add_argument("--timeout", type=float,
                        default=PHASE_TIMEOUT_DEFAULT,
                        help="per-phase seconds (default 30)")
    args = parser.parse_args(argv)

    roots = [Path(t).expanduser() for t in args.targets] \
        if args.targets else [Path.cwd()]
    targets = discover_targets(roots)

    def klass(t: dict) -> str:
        e = t["entry"]
        cmd = isinstance(e.get("command"), str) and e["command"].strip()
        url = isinstance(e.get("url"), str) and e["url"].strip()
        return "stdio" if cmd else ("remote" if url else "?")

    plan_lines = [
        f"targets: {len(targets)}",
        *[f"  would launch {klass(t)} -> {t['locator']} "
          f"({t['entry'].get('command', t['entry'].get('url', ''))})"
          for t in targets],
    ]
    if not args.yes:
        print("mcprobe plan (dry):")
        print("\n".join(plan_lines))
        print("nothing was launched")
        if sys.stdin.isatty():
            answer = input("Launch these servers? [y/N] ").strip().lower()
            if answer == "y":
                args.yes = True
        if not args.yes:
            print("refused: no execution without --yes (or interactive y)")
            return 2
    if not targets:
        print("mcprobe: no MCP targets found")
        return 2

    reports = []
    failures = 0
    for target in targets:
        klass = contact_class(target["entry"])
        if klass == "local-process":
            rep = probe_stdio(target, args.timeout)
            if rep["observation"]["reason"] in ("protocol_error", "timeout"):
                failures += 1
            reports.append(rep)
        elif klass == "remote":
            failures += 1
            reports.append({
                "locator": target["locator"],
                "transport": "remote",
                "contact": "remote",
                "launch_line_redacted": "",
                "handshake": {"initialize": "skipped",
                              "tools_list": "skipped"},
                "server_info": {},
                "observation": {"complete": False,
                                "reason": "remote_transport_not_supported_"
                                          "in_v1"},
                "tools": [],
            })

    doc = {
        "schema": SCHEMA,
        "mode": "runtime-observation",
        "deterministic": False,
        "reports": reports,
        "summary": {"probed": sum(1 for r in reports
                                  if r["handshake"]["initialize"] == "ok"),
                    "failed": failures},
    }
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
