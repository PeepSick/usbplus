#!/usr/bin/env bash
# BEFORE/AFTER comparison harness for usb-secscan calibration.
#
# Usage: bash scripts/secscan-compare.sh [cloned-repo-dir]
#
# Snapshots the CURRENT engine as BEFORE (only if no snapshot exists yet, or
# when REFRESH=1) so repeated calibration runs keep comparing against the
# same baseline instead of silently moving it. Treats the working-tree engine
# as AFTER, runs both over the SAME tree (--json, deterministic) and prints
# the delta table: totals, per-rule counts, and an explicit P-rule
# preservation check (calibration must reduce false positives, never lose
# contract hits).
set -u

REPO_DIR="${1:-/tmp/liveclone}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="$ROOT/packages/cli/bin/usb-secscan.py"
BEFORE="/tmp/usb-secscan-engine-BEFORE.py"

if [ ! -d "$REPO_DIR" ]; then
  echo "repo dir not found: $REPO_DIR (git clone a corpus first)" >&2
  exit 2
fi

if [ "${REFRESH:-0}" = "1" ] || [ ! -f "$BEFORE" ]; then
  cp "$ENGINE" "$BEFORE"
fi

python3 - "$BEFORE" "$ENGINE" "$REPO_DIR" <<'PYEOF'
import json
import subprocess
import sys

before_engine, after_engine, tree = sys.argv[1], sys.argv[2], sys.argv[3]

def scan(engine):
    r = subprocess.run([sys.executable, engine, "--json", tree],
                       capture_output=True, text=True)
    if r.returncode not in (0, 1):
        raise SystemExit(f"{engine} failed rc={r.returncode}: {r.stderr[-300:]}")
    return json.loads(r.stdout)

def tally(report):
    total_sev = {"high": 0, "medium": 0, "low": 0, "info": 0}
    by_rule = {}
    for s in report["skills"]:
        for f in s["findings"]:
            total_sev[f["severity"]] += 1
            by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    flagged = sum(1 for s in report["skills"] if s["findings"])
    return {
        "scanned": report["summary"]["skills"],
        "flagged": flagged,
        "sev": total_sev,
        "rules": by_rule,
        "paths": {s["source"]["path"] for s in report["skills"]},
    }

b = tally(scan(before_engine))
a = tally(scan(after_engine))

print("BEFORE -> AFTER  (%s)" % tree)
print()
print("Scanned:        %4d -> %4d" % (b["scanned"], a["scanned"]))
print("Flagged:        %4d -> %4d" % (b["flagged"], a["flagged"]))
for sev in ("high", "medium", "low", "info"):
    print("%-8s %4d -> %4d" % (sev.upper() + ":", b["sev"][sev], a["sev"][sev]))

print()
print("Per-rule counts:")
rules = sorted(set(b["rules"]) | set(a["rules"]))
for r in rules:
    x, y = b["rules"].get(r, 0), a["rules"].get(r, 0)
    mark = "" if x == y else ("   <- reduced" if y < x else "   <- increased")
    print("  %-6s %4d -> %4d%s" % (r, x, y, mark))

print()
lost_units = b["paths"] - a["paths"]
if lost_units:
    print("UNITS LOST ENTIRELY (%d):" % len(lost_units))
    for p in sorted(lost_units)[:10]:
        print("  -", p)
else:
    print("Units lost entirely: none")

p_before = {r: c for r, c in b["rules"].items() if r.startswith("P")}
p_after = {r: c for r, c in a["rules"].items() if r.startswith("P")}
regressed = {r: (p_before.get(r, 0), p_after.get(r, 0))
             for r in p_before if p_after.get(r, 0) < p_before.get(r, 0)}
print("P-contract preservation:",
      "OK " + str(p_after) if not regressed else f"REGRESSED {regressed}")
PYEOF
