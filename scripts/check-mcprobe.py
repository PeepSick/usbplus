#!/usr/bin/env python3
"""mcprobe conformance: consent contract + probe behaviours (offline stub).

Locks docs/mcprobe.md section 8:
  - no --yes with piped stdin -> refusal, NOTHING is launched
  - normal probe -> complete report, redaction enforced
  - repeated cursor -> partial observation, pagination_limit
  - malformed page -> protocol_error, exit 1

Run: python3 scripts/check-mcprobe.py
"""

import json
import subprocess
import sys
import tempfile
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


def run(args, stdin_text=None, timeout=90):
    r = subprocess.run([sys.executable, str(PROBE), *args],
                       input=stdin_text, capture_output=True, text=True,
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
