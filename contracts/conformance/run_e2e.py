#!/usr/bin/env python3
"""End-to-end reference corpus runner (acme-agent).

Locks the full USB v2 chain in ONE deterministic pass over a permanent
fixture tree: repository -> discovery -> config parsing -> skill scanning
-> MCP adapters -> declared tools -> permissions -> authority ->
reducer -> findings -> verdicts -> exit code.

Checks:
  1. determinism  - two engine runs (JSON report + artifact emission) are
                    byte-identical
  2. golden       - the normalized human report, JSON report and artifact
                    emission match the committed goldens exactly
  3. reducer      - the independent reducer re-derives every unit's
                    findings and verdict from the emitted artifacts alone
  4. exit codes   - the tree gates CI (exit 1: high findings present)
                    while the P103 advisory stays low and non-blocking

Normalization: the fixture tree's absolute path is replaced with <TREE>
and backslashes fold to '/', so committed goldens stay byte-stable across
machines, operating systems and temp directories.

Run: python3 contracts/conformance/run_e2e.py [--update]
     --update refreshes the committed goldens deliberately.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ENGINE = ROOT / "packages/cli/bin/usb-secscan.py"
TREE = ROOT / "contracts/fixtures/e2e/acme-agent"
GOLDENS = HERE.parent / "fixtures/e2e"

sys.path.insert(0, str(HERE))
import reduce as reducer  # noqa: E402


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def normalize(text: str) -> str:
    # Paths reach the engine in POSIX form, so no separator folding is
    # needed; replacing the machine-specific prefix is enough. JSON escape
    # sequences must survive untouched - never blanket-rewrite backslashes
    # here, they carry \", \\ and \n inside string literals.
    return text.replace(posix(TREE), "<TREE>")


def run_engine(args, outdir: Path):
    """-> (rc, stdout, artifacts_path_or_None)."""
    snap = outdir / "art.json"
    cmd = [sys.executable, str(ENGINE), *args]
    if args == ["--json"]:
        cmd += ["--emit-contracts", str(snap)]
    else:
        snap = None
    r = subprocess.run(cmd + [posix(TREE), posix(TREE / "broken/mcp.json")],
                       capture_output=True, text=True)
    return r.returncode, r.stdout, snap


def main():
    update = "--update" in sys.argv
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="usb-e2e-"))
    try:
        rc_a, json_a, art_a = run_engine(["--json"], tmp / "a")
        rc_b, json_b, art_b = run_engine(["--json"], tmp / "b")
        rc_c, human_c, _ = run_engine([], tmp / "c")

        art_a_bytes = art_a.read_bytes()
        art_b_bytes = art_b.read_bytes()

        print("-- determinism: two runs byte-identical "
              "(report + artifacts)")
        ok = json_a == json_b and art_a_bytes == art_b_bytes
        print("OK" if ok else "FAILED")
        if not ok:
            failures.append("determinism")

        print("-- exit codes: tree gates CI (1), nothing crashed")
        ok = rc_a == 1 and rc_c == 1
        print("OK" if ok else f"FAILED ({rc_a}/{rc_c})")
        if not ok:
            failures.append("exit-code")

        norm_report = normalize(json_a)
        # read_text (not bytes): the engine lets the OS pick the file's
        # line endings, so normalization folds them back to '\n' - goldens
        # stay byte-stable across Windows/Linux checkouts
        norm_artifacts = normalize(art_a.read_text(encoding="utf-8"))
        norm_human = normalize(human_c)

        print("-- advisory locked: P103 low under excessive-authority")
        try:
            report = json.loads(json_a)
            w = next(s for s in report["skills"]
                     if s["name"] == "weather-bot")
            p103 = [f for f in w["findings"] if f["rule"] == "P103"]
            ok = (w["verdict"] == "excessive-authority" and len(p103) == 1
                  and p103[0]["severity"] == "low"
                  and report["summary"]["exit_code"] == 1)
            print("OK" if ok else f"FAILED {w['verdict']} {p103}")
            if not ok:
                failures.append("advisory")
        except (StopIteration, KeyError) as exc:
            print(f"FAILED ({exc})")
            failures.append("advisory")

        goldens = {
            "human": norm_human,
            "report": norm_report,
            "artifacts": norm_artifacts,
        }
        paths = {
            "human": GOLDENS / "e2e-golden-human.txt",
            "report": GOLDENS / "e2e-golden-report.json",
            "artifacts": GOLDENS / "e2e-golden-artifacts.json",
        }
        for name, content in goldens.items():
            print(f"-- golden: {name}")
            if update:
                paths[name].write_text(content, encoding="utf-8",
                                       newline="\n")
                print("updated (deliberate)")
                continue
            if not paths[name].exists():
                print("FAILED (missing golden; run with --update)")
                failures.append(f"golden-{name}")
                continue
            ok = paths[name].read_text(encoding="utf-8") == content
            print("OK" if ok else "FAILED (drift vs committed golden)")
            if not ok:
                failures.append(f"golden-{name}")

        print("-- reducer: artifacts alone re-derive every unit")
        # reducer alignment runs on RAW output: both sides must carry the
        # identical real paths; normalization is only for golden bytes
        raw_report = json.loads(json_a)
        raw_artifacts = json.loads(art_a_bytes)
        by_path = {s["source"]["path"]: s for s in raw_report["skills"]}
        checked = 0
        mismatches = 0
        for au in raw_artifacts["units"]:
            src = au["identity"]["source"]
            if src not in by_path:
                print(f"   MISMATCH alignment failed for {src}")
                failures.append(f"alignment failed for {src}")
                continue
            problems = reducer.compare_unit(by_path[src], au)
            checked += 1
            for p in problems:
                print(f"   MISMATCH {p}")
                failures.append(p)
                mismatches += 1
        if mismatches == 0:
            print(f"OK ({checked} units)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"e2e FAILED ({len(set(failures))})")
        return 1
    print("e2e passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
