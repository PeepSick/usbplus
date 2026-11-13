#!/usr/bin/env python3
"""usb mcprobe - runtime observation for MCP servers.

NON-DETERMINISTIC by design (docs/mcprobe.md): contacts a configured MCP
server, performs the initialize/tools_list handshake, records the exposed
tools, disconnects. Never calls tools. Separate from secscan's
deterministic audit chain by construction.

Transports: stdio spawn (local-process) and streamable-HTTP / SSE
(remote-network). Remote v1 has no auth flows - 401 is recorded as
`auth_required`, 403 without WWW-Authenticate as `bot_blocked`.

Consent contract (docs/mcprobe.md section 8):
    no --yes          -> prints the plan and stops; interactive prompt only
                         on a real TTY; piped stdin counts as refusal
    --yes             -> explicit machine consent, runs immediately

Exit codes: 0 all probes contacted successfully, 1 at least one probe hit
a protocol error/timeout/auth failure/bot-block, 2 refusal / no targets / usage.
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
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SCHEMA = "usb-mcprobe-report/v1"
PROTOCOL_VERSION = "2024-11-05"
PAGE_LIMIT = 10                    # docs/mcprobe.md section 8
TOOL_CEILING = 1000
PHASE_TIMEOUT_DEFAULT = 30.0
SHUTDOWN_GRACE = 3.0
MAX_LINE = 512 * 1024
SESSION_HEADER = "Mcp-Session-Id"

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
    return SECRET_PATTERN_RE.sub("<redacted>", out)


def strip_query(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) \
        or url


class ProbeError(Exception):
    def __init__(self, phase: str, kind: str, message: str):
        super().__init__(message)
        self.phase = phase          # where it happened (handshake key)
        self.kind = kind            # observation.reason vocabulary
        self.message = message


class LineReader(threading.Thread):
    """Background reader for unsolicited-push streams (stdio pipes, SSE
    bodies): raw lines become a queue so the main thread can enforce
    deadlines."""

    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.lines: queue.Queue = queue.Queue()

    def run(self):
        try:
            for raw in self.stream:
                self.lines.put(raw.decode("utf-8", errors="replace")
                               [:MAX_LINE])
        except Exception:
            pass
        self.lines.put(None)

    def next_raw(self, deadline: float) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("timeout", "timeout",
                             "deadline exceeded while waiting for data")
        try:
            item = self.lines.get(timeout=remaining)
        except queue.Empty:
            raise ProbeError("timeout", "timeout",
                             "deadline exceeded while waiting for data")
        if item is None:
            raise ProbeError("protocol_error", "protocol_error",
                             "stream closed unexpectedly")
        return item


class StdioChannel:
    """send/recv pair over a spawned process."""

    def __init__(self, proc, timeout: float):
        self.proc = proc
        self.next_id = 1
        self.reader = LineReader(proc.stdout)
        self.reader.start()
        self.deadline = time.monotonic() + timeout

    def call(self, method: str, params: dict | None, strict: bool):
        payload: dict = {"jsonrpc": "2.0", "id": self.next_id,
                         "method": method}
        if params is not None:
            payload["params"] = params
        self.next_id += 1
        self.send(payload)
        return self.recv(None, strict)

    def notify(self, method: str):
        self.send({"jsonrpc": "2.0", "method": method})

    def send(self, payload: dict):
        self.proc.stdin.write(
            (json.dumps(payload, ensure_ascii=False) + "\n")
            .encode("utf-8"))
        self.proc.stdin.flush()

    def recv(self, msg_id: int | None, strict: bool):
        del msg_id  # stdio frames arrive in order; id matching implicit
        while True:
            line = self.reader.next_raw(self.deadline).strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                if strict:
                    raise ProbeError("tools_list", "protocol_error",
                                     "malformed JSON-RPC frame")
                continue
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return msg


class HttpChannel:
    """send/recv pair over streamable-HTTP or SSE envelopes."""

    def __init__(self, url: str, base_headers: dict, mode: str,
                 timeout: float):
        self.url = url
        self.mode = mode              # "remote-http" | "remote-sse"
        self.timeout = timeout
        self.headers = {"User-Agent": "usb-mcprobe/0.1 (+security audit)",
                        **dict(base_headers)}
        self.session_id = None
        self.session_present = False
        self.deadline = time.monotonic() + timeout
        self.next_id = 1

    def _post(self, payload: dict | None):
        req = urllib.request.Request(self.url, method="POST")
        for k, v in self.headers.items():
            req.add_header(k, v)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self.session_id:
            req.add_header(SESSION_HEADER, self.session_id)
        body = (json.dumps(payload, ensure_ascii=False) + "\n") \
            .encode("utf-8") if payload is not None else b""
        return urllib.request.urlopen(req, data=body,
                                      timeout=max(1.0, self.deadline
                                                  - time.monotonic()))

    @staticmethod
    def _message_from_body(body: str, content_type: str):
        if "text/event-stream" in content_type:
            datas = []
            for line in body.splitlines():
                if line.startswith("data:"):
                    datas.append(line[5:].lstrip())
            joined = "\n".join(datas).strip()
            if not joined:
                return None
            return json.loads(joined)
        return json.loads(body) if body.strip() else None

    def exchange(self, payload: dict, strict: bool):
        try:
            resp = self._post(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                kind = "auth_required"
            elif exc.code == 403:
                # Distinguish genuine auth failure from WAF/bot-block.
                # 403 + WWW-Authenticate = server rejected credentials.
                # 403 without it = Cloudflare/WAF challenge page.
                wa = exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
                kind = "auth_required" if wa else "bot_blocked"
            else:
                kind = "protocol_error"
            exc.close()
            raise ProbeError("handshake" if payload.get("method") ==
                             "initialize" else "tools_list",
                             kind,
                             f"HTTP {exc.code}")
        except urllib.error.URLError as exc:
            raise ProbeError("connect", "protocol_error",
                             f"connection failed: {exc.reason}")
        with resp:
            ctype = resp.headers.get("Content-Type", "")
            sid = resp.headers.get(SESSION_HEADER)
            if sid:
                self.session_id = sid
                self.session_present = True
            if "text/event-stream" not in ctype:
                body = resp.read(MAX_LINE).decode("utf-8", errors="replace")
                msg = self._message_from_body(body, ctype)
                if msg is None:
                    if strict:
                        raise ProbeError("tools_list", "protocol_error",
                                         "empty response body")
                    return None
                if strict and isinstance(msg, dict) \
                        and "result" not in msg and "error" not in msg:
                    raise ProbeError("tools_list", "protocol_error",
                                     "unexpected response shape")
                return msg
            reader = LineReader(resp)
            reader.start()
            datas = []
            while True:
                line = reader.next_raw(min(self.deadline,
                                           time.monotonic() + self.timeout))
                if line.startswith("data:"):
                    datas.append(line[5:].strip())
                elif line.strip() == "" and datas:
                    break
            joined = "\n".join(datas).strip()
            try:
                msg = json.loads(joined)
            except ValueError:
                if strict:
                    raise ProbeError("tools_list", "protocol_error",
                                     "malformed SSE frame")
                return None
            if strict and isinstance(msg, dict) \
                    and "result" not in msg and "error" not in msg:
                raise ProbeError("tools_list", "protocol_error",
                                 "unexpected SSE message")
            return msg

    def call(self, method: str, params: dict | None, strict: bool):
        payload: dict = {"jsonrpc": "2.0", "id": self.next_id,
                         "method": method}
        if params is not None:
            payload["params"] = params
        self.next_id += 1
        return self.exchange(payload, strict=strict)

    def notify(self, method: str):
        self.exchange({"jsonrpc": "2.0", "method": method}, strict=False)

    def close_session(self):
        if not self.session_id:
            return
        try:
            req = urllib.request.Request(self.url, method="DELETE")
            req.add_header(SESSION_HEADER, self.session_id)
            urllib.request.urlopen(req, timeout=SHUTDOWN_GRACE).close()
        except Exception:
            pass


def _paginate(channel, deadline: float, secrets: list[tuple[str, str]]):
    """Shared tools/list loop (page cap, tool ceiling, repeated-cursor
    stop). Returns (sorted tools, observation dict)."""
    cursor = None
    seen_cursors = set()
    pages = 0
    tools = []
    complete, reason = True, None
    while True:
        params = {} if cursor is None else {"cursor": cursor}
        resp = channel.call("tools/list", params, strict=True)
        if "error" in resp:
            err = resp["error"]
            raise ProbeError("tools_list", "protocol_error",
                             f"server returned JSON-RPC error: "
                             f"{err.get('message', '')}"[:200])
        payload = resp.get("result") or {}
        page_tools = payload.get("tools")
        if not isinstance(page_tools, list):
            raise ProbeError("tools_list", "protocol_error",
                             "malformed tools page")
        pages += 1
        for t in page_tools[:max(0, TOOL_CEILING - len(tools))]:
            if not isinstance(t, dict) or not isinstance(t.get("name"), str):
                raise ProbeError("tools_list", "protocol_error",
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
        if time.monotonic() > deadline:
            raise ProbeError("tools_list", "timeout",
                             "deadline exceeded during pagination")
    return sorted(tools, key=lambda t: t["name"]), \
        {"complete": complete, "reason": reason}


def discover_targets(roots: list[Path]):
    targets = []
    for root in roots:
        if not root.exists():
            continue
        for rel, cfg_path in ENGINE.find_mcp_configs(root):
            try:
                raw = cfg_path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            lines = (raw.decode("utf-8", errors="replace")
                     .lstrip("\ufeff").splitlines())
            servers, _structural = ENGINE.parse_mcp_config(lines)
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


def transports_for(entry: dict):
    cmd = isinstance(entry.get("command"), str) and entry["command"].strip()
    url = isinstance(entry.get("url"), str) and entry["url"].strip()
    etype = entry.get("type") if isinstance(entry.get("type"), str) else ""
    if cmd:
        return "local-process", "stdio"
    if url:
        mode = "remote-sse" if etype.lower() == "sse" else "remote-http"
        return "remote-network", mode
    return None, None


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

    # docs/mcprobe.md: spawn(command, args, env) # cwd: config dir.
    # The child resolves its OWN relative args (scripts, data files, .env)
    # against its cwd, so it must run in the directory of the config file
    # that declares it - not in mcprobe's own cwd.
    cwd = None
    cfg = target["locator"].split("#", 1)[0]
    if cfg:
        cfg_path = Path(cfg)
        if not cfg_path.is_absolute():
            cfg_path = Path.cwd() / cfg_path
        if cfg_path.parent.is_dir():
            cwd = str(cfg_path.parent)
    if cwd is None:
        # locator without a usable config path: keep the legacy heuristic
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
        "session_id_present": None,
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

    channel = StdioChannel(proc, timeout)
    try:
        channel.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {
                          "protocolVersion": PROTOCOL_VERSION,
                          "capabilities": {},
                          "clientInfo": {"name": "usb-mcprobe",
                                         "version": "0.1.0"}}})
        init = channel.recv(1, strict=False)
        if "error" in init:
            raise ProbeError("initialize", "protocol_error",
                             "server returned JSON-RPC error")
        info = (init.get("result") or {}).get("serverInfo") or {}
        report["server_info"] = {
            "name": str(info.get("name", ""))[:120],
            "version": str(info.get("version", ""))[:40],
        }
        report["handshake"]["initialize"] = "ok"
        channel.send({"jsonrpc": "2.0",
                      "method": "notifications/initialized"})
        report["handshake"]["initialize"] = "ok"

        tools, obs = _paginate(channel, channel.deadline,
                               [(k, v) for k, v in declared_env.items()])
        report["tools"] = tools
        report["observation"] = obs
        report["handshake"]["tools_list"] = "ok"
    except ProbeError as exc:
        report["handshake"][exc.phase] = (
            f"{exc.kind}: {exc.message}"[:300])
        report["observation"] = {"complete": False, "reason": exc.kind}
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


def probe_remote(target: dict, timeout: float) -> dict:
    entry = target["entry"]
    url_raw = entry["url"].strip()
    url = strip_query(url_raw)
    _, transport = transports_for(entry)
    headers = {}
    hdrs = entry.get("headers") if isinstance(entry.get("headers"), dict) \
        else {}
    secrets = [("headers", str(v)) for v in hdrs.values()]
    for k, v in hdrs.items():
        if isinstance(v, str):
            headers[k] = v
            secrets.append((str(k), v))
    denv = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    secrets.extend((str(k), str(v)) for k, v in denv.items())

    report = {
        "locator": target["locator"],
        "transport": transport,
        "contact": "remote-network",
        "launch_line_redacted": redact(url, secrets),
        "session_id_present": False,
        "handshake": {"connect": "skipped", "handshake": "skipped",
                      "tools_list": "skipped"},
        "server_info": {},
        "observation": {"complete": False, "reason": None},
        "tools": [],
    }
    channel = HttpChannel(url, headers, transport, timeout)
    try:
        init = channel.call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "usb-mcprobe", "version": "0.1.0"},
        }, strict=False)
        report["handshake"] = {"connect": "ok", "handshake": "ok",
                               "tools_list": "pending"}
        report["session_id_present"] = channel.session_present
        if "error" in init:
            raise ProbeError("handshake", "protocol_error",
                             "server returned JSON-RPC error")
        info = (init.get("result") or {}).get("serverInfo") or {}
        report["server_info"] = {
            "name": str(info.get("name", ""))[:120],
            "version": str(info.get("version", ""))[:40],
        }
        channel.notify("notifications/initialized")

        tools, obs = _paginate(channel, channel.deadline, secrets)
        report["tools"] = tools
        report["observation"] = obs
        report["handshake"]["tools_list"] = "ok"
    except ProbeError as exc:
        phase = exc.phase
        if phase == "handshake":
            report["handshake"]["handshake"] = f"{exc.kind}: {exc.message}"
            report["handshake"]["connect"] = report["handshake"].get(
                "connect", "ok")
            if "connect" not in report["handshake"]:
                report["handshake"]["connect"] = "skipped"
        elif phase == "connect":
            report["handshake"]["connect"] = f"{exc.kind}: {exc.message}"
        elif phase == "tools_list":
            report["handshake"]["tools_list"] = f"{exc.kind}: {exc.message}"
        report["observation"] = {"complete": False, "reason": exc.kind}
    finally:
        channel.close_session()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="usb mcprobe",
        description="Runtime observation of MCP servers - contacts the "
                    "target, lists its tools, disconnects. NON-deterministic "
                    "and opt-in by design.")
    parser.add_argument("targets", nargs="*",
                        help="config paths or scan roots (same discovery as "
                             "secscan)")
    parser.add_argument("--yes", action="store_true",
                        help="explicit machine consent to contact targets")
    parser.add_argument("--json", action="store_true", default=True)
    parser.add_argument("--timeout", type=float,
                        default=PHASE_TIMEOUT_DEFAULT,
                        help="per-phase seconds (default 30)")
    args = parser.parse_args(argv)

    roots = [Path(t).expanduser() for t in args.targets] \
        if args.targets else [Path.cwd()]
    targets = discover_targets(roots)

    def klass(t):
        contact, transport = transports_for(t["entry"])
        return transport or "?"

    plan_lines = [
        f"targets: {len(targets)}",
        *[f"  would contact {klass(t)} -> {t['locator']} "
          f"({t['entry'].get('command', t['entry'].get('url', ''))})"
          for t in targets],
    ]
    if not args.yes:
        print("mcprobe plan (dry):")
        print("\n".join(plan_lines))
        print("nothing was contacted")
        if sys.stdin.isatty():
            try:
                answer = input("Contact these servers? [y/N] ").strip().lower()
                if answer == "y":
                    args.yes = True
            except (EOFError, OSError):
                pass  # broken/absent interactive stream counts as refusal
        if not args.yes:
            print("refused: no execution without --yes (or interactive y)")
            return 2
    if not targets:
        print("mcprobe: no MCP targets found")
        return 2

    reports = []
    failures = 0
    for target in targets:
        contact, transport = transports_for(target["entry"])
        if contact is None:
            failures += 1
            reports.append({
                "locator": target["locator"], "transport": "?",
                "contact": "unknown",
                "launch_line_redacted": "",
                "session_id_present": None,
                "handshake": {},
                "server_info": {}, "tools": [],
                "observation": {"complete": False,
                                "reason": "no_command_or_url"},
            })
            continue
        if contact == "local-process":
            rep = probe_stdio(target, args.timeout)
        else:
            rep = probe_remote(target, args.timeout)
        reason = rep["observation"]["reason"]
        if reason in ("protocol_error", "timeout", "auth_required",
                       "bot_blocked") \
                or rep["handshake"].get("initialize", "").startswith(("error",
                                                                      "spawn")):
            failures += 1
        reports.append(rep)

    doc = {
        "schema": SCHEMA,
        "mode": "runtime-observation",
        "deterministic": False,
        "reports": reports,
        "summary": {"probed": sum(1 for r in reports
                                  if r["handshake"].get("initialize")
                                  in ("ok",)),
                    "failed": failures},
    }
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
