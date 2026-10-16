#!/usr/bin/env python3
"""Migration Step 1 conformance runner.

Proves, on a permanent fixture tree:

  1. determinism  - two artifact emissions over the same tree are
                    byte-identical, and match the committed golden snapshot
  2. completeness - the four artifacts alone re-derive the engine's
                    findings and verdicts (independent reducer in reduce.py)

Run: python3 contracts/conformance/run_conformance.py [--update]
     --update refreshes the committed golden snapshot deliberately.
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
TREE = HERE.parent / "fixtures/trees/skills-project"
GOLDEN = HERE.parent / "fixtures/artifacts-golden.json"

sys.path.insert(0, str(HERE))
import reduce as reducer  # noqa: E402


def run_engine(extra, outdir: Path):
    """-> (rc, json_report_dict_or_None, artifacts_path)."""
    snap = outdir / "artifacts.json"
    cmd = [sys.executable, str(ENGINE), "--json",
           "--emit-contracts", str(snap),
           *extra, str(TREE)]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        data = None
    return r.returncode, data, snap


def main():
    if "--update" not in sys.argv and GOLDEN.exists():
        update = False
    else:
        update = True

    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="usb-conformance-"))
    try:
        rc1, report1, snap1 = run_engine([], tmp / "run1")
        rc2, _report2, snap2 = run_engine([], tmp / "run2")

        print("-- determinism: two emissions byte-identical")
        ok = snap1.read_bytes() == snap2.read_bytes()
        print("OK" if ok else f"FAILED (rc {rc1}/{rc2})")
        if not ok:
            failures.append("determinism")
        if rc1 != 1:
            print(f"-- fixture tree expected exit 1, got {rc1}")
            failures.append("exit-code")

        print("-- golden snapshot")
        if update:
            shutil.copyfile(snap1, GOLDEN)
            print("updated (deliberate)")
        else:
            ok = snap1.read_bytes() == GOLDEN.read_bytes()
            print("OK" if ok else "FAILED (drift vs committed golden)")
            if not ok:
                failures.append("golden-drift")

        print("-- semantic compare: artifacts -> reduced findings vs engine")
        if report1 is None:
            failures.append("engine json unparsable")
        else:
            by_path = {s["source"]["path"]: s for s in report1["skills"]}
            checked = 0
            for au in json.loads(snap1.read_text(encoding="utf-8"))["units"]:
                src = au["identity"]["source"]
                if src not in by_path:
                    failures.append(f"alignment failed for {src}")
                    continue
                problems = reducer.compare_unit(by_path[src], au)
                checked += 1
                for p in problems:
                    print(f"   MISMATCH {p}")
                    failures.append(p)
            print(f"OK ({checked} units)" if not failures else "")

        print("-- schema check")
        doc = json.loads(snap1.read_text(encoding="utf-8"))
        try:
            reducer.check_schema(doc)
            print("OK")
        except ValueError as exc:
            print(f"FAILED: {exc}")
            failures.append(str(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"conformance FAILED ({len(set(failures))})")
        return 1
    print("conformance passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
