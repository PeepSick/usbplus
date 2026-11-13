#!/usr/bin/env python3
"""usb integrity engine — verify / lock / diff for installed USB skills.

The Trust Layer's integrity pillar. `usb secscan` answers "does this skill
do what it declares"; this engine answers "is what is on disk still what
was installed". It verifies file integrity, NOT content safety — a
perfectly intact skill can still be malicious, so secscan stays separate.

Subcommands
-----------
    verify [slug] [--all] [--json] [--target T] [--pack-dir D]
        Re-hash installed files against the install-time SHA-256 manifest
        (usb-manifest.json, written by the installer) and report modified,
        missing and unexpected files. Checks the portable pack plus every
        runtime target copy the installer would have written (claude,
        leosis, hermes, cursor, adapter dirs), honoring the same *_DIR env
        overrides as the installer.

    lock [--file F] [--target T] [--pack-dir D]
        Pin the installed skill set into usb.lock.json (pack version,
        target, per-skill SHA-256). With a manifest the pinned hashes are
        the install-time ones; without one the current files are hashed
        (trust-on-first-use) so older installs can still be pinned.

    lock-check --staging D [--file F] [--json]
        Compare a staged pack directory against a lock file. Used by
        `usb install --locked` to refuse drifted catalog content before
        anything is written to the real install locations.

    lock-show [--file F]
        Print a validated lock file as JSON (for scripts).

    diff OLD NEW [--json] [--patch]
        Compare two skill versions (files, or directories containing
        SKILL.md / a single .md): frontmatter fields, declared tools,
        observed/declared capability categories and the secscan verdict.
        Capability analysis is reused from usb-secscan.py so both tools
        always agree on what a capability is.

Exit codes (every subcommand)
-----------------------------
    0   clean / identical / success
    1   drift or differences found
    2   operational error (bad arguments, nothing installed, unreadable)

Determinism: verify/diff reports contain no timestamps and are fully
ordered; lock files record locked_at because they are a record, not a
report. stdout is UTF-8 pinned; colors only on a TTY with NO_COLOR unset.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"
TOOL = "usb-integrity"
MANIFEST_NAME = "usb-manifest.json"
LOCK_NAME = "usb.lock.json"
DEFAULT_BASE_URL = "https://usbplus.peepsicklabs.com"
SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")
PATCH_MAX_LINES = 200

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

ADAPTER_JSON_TARGETS = ("openai", "anthropic", "openrouter", "groq",
                        "mistral", "langchain", "mcp")
ADAPTER_MD_TARGETS = ("ollama", "lm-studio", "vllm")

if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
    RED, GRN, YEL, RST = "\033[0;31m", "\033[0;32m", "\033[0;33m", "\033[0m"
else:
    RED = GRN = YEL = RST = ""


def err(msg):
    print(f"{RED}error:{RST} {msg}", file=sys.stderr)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def short_hash(h):
    return (h[:19] + "…") if h and len(h) > 19 else (h or "-")


def read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except OSError as e:
        return None, f"cannot read {path}: {e}"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in {path}: {e}"


def env_dir(name, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw if raw else str(default)).expanduser()


def ai_root() -> Path:
    return env_dir("AI_SKILL_HOME", Path.home() / ".ai-skills")


def discover_packs(require_manifest):
    root = ai_root()
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if require_manifest:
            if (p / MANIFEST_NAME).is_file():
                out.append(p)
        elif (p / MANIFEST_NAME).is_file() or (p / "skills").is_dir():
            out.append(p)
    return out


def pick_pack(args, require_manifest):
    if getattr(args, "pack_dir", None):
        p = Path(args.pack_dir).expanduser()
        if not p.is_dir():
            err(f"pack dir does not exist: {p}")
            return None, EXIT_ERROR
        if require_manifest and not (p / MANIFEST_NAME).is_file():
            err(f"no {MANIFEST_NAME} in {p} — reinstall with usb install to create one")
            return None, EXIT_ERROR
        return p, EXIT_OK
    packs = discover_packs(require_manifest)
    if not packs:
        err("nothing installed: no USB pack found under "
            f"{ai_root()} — run usb install first")
        return None, EXIT_ERROR
    if len(packs) > 1:
        err("multiple packs installed: " + ", ".join(p.name for p in packs))
        err("pass --pack-dir <dir> to choose one")
        return None, EXIT_ERROR
    return packs[0], EXIT_OK


def load_manifest(pack_dir: Path):
    data, e = read_json(pack_dir / MANIFEST_NAME)
    if e:
        return None, e
    if not isinstance(data, dict) or data.get("schema") != 1 \
            or not isinstance(data.get("files"), dict) \
            or not isinstance(data.get("pack"), str) \
            or not isinstance(data.get("version"), str):
        return None, f"unsupported {MANIFEST_NAME} schema in {pack_dir}"
    for rel, h in data["files"].items():
        if not isinstance(h, str) or not h.startswith("sha256:"):
            return None, f"bad hash for {rel} in {MANIFEST_NAME}"
    return data, None


def skill_entries(manifest):
    """-> sorted [(slug, rel, hash)] for the manifest's skill files."""
    out = []
    for rel, h in manifest["files"].items():
        if rel.startswith("skills/") and rel.endswith(".md"):
            slug = rel[len("skills/"):-len(".md")]
            if SLUG_RE.match(slug):
                out.append((slug, rel, h))
    return sorted(out)


def target_layouts(manifest):
    """Mirror renderInstallScript()'s per-target copies, env overrides
    included. -> [(target, dest, [(path, expected_hash, slug_or_None, rel)])].
    Claude lists BOTH live layouts per skill (small <slug>/SKILL.md dirs and
    the catalog usb/skills/ router); verify checks whichever exists, exactly
    like uninstall. Pack-level hashes are fallbacks: the target ledger
    (usb-targets.txt) carries the per-run hashes when present."""
    home = Path.home()
    root = ai_root()
    pack = manifest["pack"]
    files = manifest["files"]
    skills = skill_entries(manifest)
    layouts = []

    dest = env_dir("LEOSIS_SKILLS_DIR", home / ".leosis" / "skills" / pack)
    entries = [(dest / f"{s}.md", h, s, f"{s}.md") for s, _, h in skills]
    entries += [
        (dest / "skillpack.json", files["skillpack.json"], None, "skillpack.json"),
        (dest / "adapter.bridge.json", files["adapter.bridge.json"], None, "adapter.bridge.json"),
        (dest / f"{pack}.mdc", files["cursor-rule.mdc"], None, f"{pack}.mdc"),
    ]
    layouts.append(("leosis", dest, entries))

    dest = env_dir("CLAUDE_SKILLS_DIR", home / ".claude" / "skills")
    entries = []
    for s, _, h in skills:
        entries.append((dest / s / "SKILL.md", h, s, f"{s}/SKILL.md"))
        entries.append((dest / "usb" / "skills" / f"{s}.md", h, s, f"usb/skills/{s}.md"))
    entries += [
        (dest / "usb" / "skillpack.json", files["skillpack.json"], None, "usb/skillpack.json"),
        (dest / "usb" / "SKILL.md", "", None, "usb/SKILL.md"),
    ]
    layouts.append(("claude", dest, entries))

    dest = env_dir("HERMES_SKILLS_DIR", home / ".hermes" / "skills" / pack)
    entries = [(dest / f"{s}.md", h, s, f"{s}.md") for s, _, h in skills]
    entries += [
        (dest / "hermes.skillpack.json", files["skillpack.json"], None, "hermes.skillpack.json"),
        (dest / "adapter.bridge.json", files["adapter.bridge.json"], None, "adapter.bridge.json"),
    ]
    layouts.append(("hermes", dest, entries))

    # Cursor rules are per-project: only the env override or the current
    # working directory's copy is visible from here.
    dest = env_dir("CURSOR_RULES_DIR", Path.cwd() / ".cursor" / "rules")
    layouts.append(("cursor", dest, [
        (dest / f"{pack}.mdc", files["cursor-rule.mdc"], None, f"{pack}.mdc")]))

    for t in ADAPTER_JSON_TARGETS:
        dest = root / "adapters" / t
        layouts.append((t, dest, [
            (dest / f"{pack}.json", files["adapter.bridge.json"], None, f"{pack}.json"),
            (dest / "skillpack.json", files["skillpack.json"], None, "skillpack.json"),
        ]))
    for t in ADAPTER_MD_TARGETS:
        dest = root / "adapters" / t
        entries = [(dest / f"{s}.md", h, s, f"{s}.md") for s, _, h in skills]
        entries += [
            (dest / f"{pack}.json", files["adapter.bridge.json"], None, f"{pack}.json"),
            (dest / "skillpack.json", files["skillpack.json"], None, "skillpack.json"),
        ]
        layouts.append((t, dest, entries))
    return layouts


def read_ledger(pack_dir: Path):
    """usb-targets.txt: append-only lines 'target:slug' (skill presence) and
    'target:file:rel:sha256:hex' (per-run pack-level hashes). Last line per
    key wins. -> (presence, file_hashes) or None when absent."""
    path = pack_dir / "usb-targets.txt"
    if not path.is_file():
        return None
    presence = {}
    file_hashes = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split(":")
        if len(parts) == 2 and SLUG_RE.match(parts[1]):
            presence.setdefault(parts[0], set()).add(parts[1])
        elif len(parts) >= 4 and parts[1] == "file":
            file_hashes[(parts[0], parts[2])] = ":".join(parts[3:])
    return presence, file_hashes


def unexpected_files(directory: Path, expected):
    """Files under a pack-owned directory that the installer never wrote."""
    if not directory.is_dir():
        return []
    expected = {Path(p).resolve() for p in expected}
    out = []
    for f in sorted(directory.rglob("*")):
        if f.is_file() and f.resolve() not in expected:
            out.append(f)
    return out


def check_file(path: Path, expected, slug, issues):
    if not path.exists():
        issues.append({"kind": "missing", "path": str(path),
                       "expected": expected, "actual": None, "slug": slug})
        return
    try:
        actual = sha256_file(path)
    except OSError:
        issues.append({"kind": "unreadable", "path": str(path),
                       "expected": expected, "actual": None, "slug": slug})
        return
    if actual != expected:
        issues.append({"kind": "modified", "path": str(path),
                       "expected": expected, "actual": actual, "slug": slug})


# --- verify -----------------------------------------------------------------

def cmd_verify(args):
    if args.slug and not SLUG_RE.match(args.slug):
        err(f"invalid slug: '{args.slug}' (only lowercase letters, digits and dashes)")
        return EXIT_ERROR
    packs = discover_packs(require_manifest=True)
    if args.pack_dir:
        p = Path(args.pack_dir).expanduser()
        packs = [p] if (p / MANIFEST_NAME).is_file() else []
        if not packs:
            err(f"no {MANIFEST_NAME} in {p} — reinstall with usb install to create one")
            return EXIT_ERROR
    if not packs:
        err(f"no integrity manifest found under {ai_root()}")
        err("reinstall with usb install to create one, or pin the current")
        err("state with usb lock (trust-on-first-use)")
        return EXIT_ERROR

    all_packs = []
    for pack_dir in packs:
        manifest, e = load_manifest(pack_dir)
        if e:
            err(e)
            return EXIT_ERROR
        skills = skill_entries(manifest)
        if args.slug:
            skills = [s for s in skills if s[0] == args.slug]
            if not skills:
                err(f"'{args.slug}' is not in the manifest of {pack_dir.name}")
                return EXIT_ERROR
        issues = []

        for rel, h in sorted(manifest["files"].items()):
            slug = None
            if rel.startswith("skills/") and rel.endswith(".md"):
                slug = rel[len("skills/"):-len(".md")]
                if args.slug and slug != args.slug:
                    continue
            check_file(pack_dir / rel, h, slug, issues)

        # Unexpected files: the pack dir and pack-owned target dirs only.
        # Shared dirs (~/.claude/skills root, adapter dirs) hold other
        # people's files and are never walked for extras.
        if not args.slug:
            allowed_top = {"skills", "installed.json", MANIFEST_NAME,
                           "usb-targets.txt"}
            allowed_top |= {rel.split("/")[0] for rel in manifest["files"]}
            for entry in sorted(pack_dir.iterdir()):
                if entry.name not in allowed_top:
                    issues.append({"kind": "unexpected", "path": str(entry),
                                   "expected": None, "actual": None,
                                   "slug": None})
            known = {rel for _, rel, _ in skill_entries(manifest)}
            skills_dir = pack_dir / "skills"
            if skills_dir.is_dir():
                for f in sorted(skills_dir.rglob("*")):
                    if f.is_file() and f.relative_to(pack_dir).as_posix() not in known:
                        slug = f.stem if SLUG_RE.match(f.stem) else None
                        issues.append({"kind": "unexpected", "path": str(f),
                                       "expected": None, "actual": None,
                                       "slug": slug})

        ledger = read_ledger(pack_dir)
        for target, dest, entries in target_layouts(manifest):
            if args.target and target != args.target:
                continue
            if ledger is not None:
                presence, fhashes = ledger
                if target not in presence \
                        and not any(t == target for t, _ in fhashes):
                    continue
                slugs_here = presence.get(target, set())
            else:
                if not any(p.exists() for p, _, _, _ in entries):
                    continue
                slugs_here = {s for _, _, s, _ in entries if s is not None}
                fhashes = {}
            skill_paths = {}
            for p, h, slug, rel in entries:
                if slug is None:
                    if args.slug:
                        continue
                    if ledger is not None:
                        if (target, rel) not in fhashes:
                            continue
                        h = fhashes[(target, rel)]
                    if not h:
                        continue
                    check_file(p, h, None, issues)
                else:
                    if slug not in slugs_here:
                        continue
                    if args.slug and slug != args.slug:
                        continue
                    skill_paths.setdefault(slug, []).append((p, h))
            for slug, cand in sorted(skill_paths.items()):
                existing = [(p, h) for p, h in cand if p.exists()]
                if existing:
                    for p, h in existing:
                        check_file(p, h, slug, issues)
                else:
                    check_file(cand[0][0], cand[0][1], slug, issues)
            if args.slug:
                continue
            if target in ("leosis", "hermes"):
                for f in unexpected_files(dest, [p for p, _, _, _ in entries]):
                    issues.append({"kind": "unexpected", "path": str(f),
                                   "expected": None, "actual": None,
                                   "slug": f.stem if SLUG_RE.match(f.stem) else None})
            elif target == "claude":
                for s, _, _ in skill_entries(manifest):
                    d = dest / s
                    if d.is_dir():
                        for f in unexpected_files(d, [d / "SKILL.md"]):
                            issues.append({"kind": "unexpected", "path": str(f),
                                           "expected": None, "actual": None,
                                           "slug": s})
                usb_dir = dest / "usb"
                for f in unexpected_files(usb_dir, [p for p, _, _, _ in entries]):
                    issues.append({"kind": "unexpected", "path": str(f),
                                   "expected": None, "actual": None,
                                   "slug": f.stem if SLUG_RE.match(f.stem) else None})

        by_slug = {s: {"slug": s, "status": "OK", "issues": []}
                   for s, _, _ in skills}
        pack_issues = []
        for iss in issues:
            slug = iss["slug"]
            if slug is not None and slug in by_slug:
                by_slug[slug]["issues"].append(iss)
            elif slug is not None:
                by_slug[slug] = {"slug": slug, "status": "WARN", "issues": [iss]}
            else:
                pack_issues.append(iss)
        for entry in by_slug.values():
            kinds = {i["kind"] for i in entry["issues"]}
            if kinds & {"missing", "unreadable"}:
                entry["status"] = "ERROR"
            elif kinds:
                entry["status"] = "WARN"

        all_packs.append({
            "pack": manifest["pack"],
            "version": manifest["version"],
            "dir": str(pack_dir),
            "skills": [by_slug[s] for s in sorted(by_slug)],
            "pack_issues": pack_issues,
        })

    summary = summarize(all_packs)
    if args.json:
        print(json.dumps({"tool": TOOL, "version": VERSION,
                          "packs": all_packs, "summary": summary},
                         sort_keys=True, indent=2))
    else:
        human_verify(all_packs, summary)
    return EXIT_DRIFT if summary["drift"] else EXIT_OK


def summarize(all_packs):
    checked = clean = modified = missing = unexpected = 0
    pack_issue_count = 0
    for p in all_packs:
        for s in p["skills"]:
            checked += 1
            kinds = {i["kind"] for i in s["issues"]}
            if not kinds:
                clean += 1
            if "modified" in kinds:
                modified += 1
            if kinds & {"missing", "unreadable"}:
                missing += 1
            unexpected += sum(1 for i in s["issues"] if i["kind"] == "unexpected")
        pack_issue_count += len(p["pack_issues"])
        unexpected += sum(1 for i in p["pack_issues"] if i["kind"] == "unexpected")
    return {
        "skills_checked": checked,
        "clean": clean,
        "modified": modified,
        "missing": missing,
        "unexpected": unexpected,
        "pack_issues": pack_issue_count,
        "drift": bool(modified or missing or unexpected or pack_issue_count),
    }


def print_issue(iss):
    kind = iss["kind"]
    if kind == "modified":
        print(f"         Modified: {iss['path']}")
        print(f"           expected: {short_hash(iss['expected'])}")
        print(f"           actual:   {short_hash(iss['actual'])}")
    elif kind in ("missing", "unreadable"):
        label = "Missing" if kind == "missing" else "Unreadable"
        print(f"         {label}: {iss['path']}")
    else:
        print(f"         Unexpected: {iss['path']}")


def human_verify(all_packs, summary):
    print("USB Integrity Verification")
    for p in all_packs:
        print()
        print(f"pack: {p['pack']} v{p['version']}")
        print(f"dir:  {p['dir']}")
        print()
        for s in p["skills"]:
            label = f"[{s['status']}]".ljust(8)
            if RST:
                color = {"OK": GRN, "WARN": YEL, "ERROR": RED}[s["status"]]
                label = label.replace(f"[{s['status']}]",
                                      f"{color}[{s['status']}]{RST}")
            print(f"{label} {s['slug']}")
            for iss in s["issues"]:
                print_issue(iss)
        if p["pack_issues"]:
            label = "[PACK]".ljust(8)
            if RST:
                label = label.replace("[PACK]", f"{YEL}[PACK]{RST}")
            print(f"{label} pack-level files")
            for iss in p["pack_issues"]:
                print_issue(iss)
    print()
    print("Summary:")
    print(f"  Skills checked: {summary['skills_checked']}")
    print(f"  Clean:          {summary['clean']}")
    print(f"  Modified:       {summary['modified']}")
    print(f"  Missing:        {summary['missing']}")
    print(f"  Unexpected:     {summary['unexpected']}")
    if summary["pack_issues"]:
        print(f"  Pack issues:    {summary['pack_issues']}")


# --- lock -------------------------------------------------------------------

def installed_meta(pack_dir):
    data, _ = read_json(pack_dir / "installed.json")
    return data if isinstance(data, dict) else {}


def cmd_lock(args):
    pack_dir, rc = pick_pack(args, require_manifest=False)
    if rc:
        return rc
    manifest, _ = load_manifest(pack_dir)
    if manifest:
        skills = {s: h for s, _, h in skill_entries(manifest)}
        version = manifest["version"]
        source = "install-time manifest"
    else:
        skills = {}
        skills_dir = pack_dir / "skills"
        if skills_dir.is_dir():
            for f in sorted(skills_dir.glob("*.md")):
                if SLUG_RE.match(f.stem):
                    try:
                        skills[f.stem] = sha256_file(f)
                    except OSError:
                        err(f"cannot read {f}")
                        return EXIT_ERROR
        version = installed_meta(pack_dir).get("version", "unknown")
        source = "current files (trust-on-first-use, no manifest)"
    if not skills:
        err(f"no skills found in {pack_dir}")
        return EXIT_ERROR
    meta = installed_meta(pack_dir)
    target = args.target or meta.get("target") or "generic"
    lock = {
        "schema": 1,
        "pack": manifest["pack"] if manifest else pack_dir.name,
        "version": version,
        "target": target,
        "base_url": os.environ.get("USB_BASE_URL", DEFAULT_BASE_URL),
        "locked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "skills": skills,
    }
    out = Path(args.file).expanduser()
    try:
        out.write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n",
                       encoding="utf-8")
    except OSError as e:
        err(f"cannot write {out}: {e}")
        return EXIT_ERROR
    print(f"{GRN}ok:{RST} locked {len(skills)} skill(s) from {source}")
    print(f"  pack:    {lock['pack']} v{version}")
    print(f"  target:  {target}")
    print(f"  file:    {out}")
    print("  install reproducibly with: usb install --locked")
    return EXIT_OK


def load_lock(path: Path):
    data, e = read_json(path)
    if e:
        return None, e
    if not isinstance(data, dict) or data.get("schema") != 1 \
            or not isinstance(data.get("skills"), dict) \
            or not isinstance(data.get("pack"), str) \
            or not isinstance(data.get("target"), str):
        return None, f"unsupported lock schema in {path}"
    for slug, h in data["skills"].items():
        if not SLUG_RE.match(slug) or not isinstance(h, str) \
                or not h.startswith("sha256:"):
            return None, f"bad lock entry for '{slug}' in {path}"
    return data, None


def cmd_lock_show(args):
    lock, e = load_lock(Path(args.file).expanduser())
    if e:
        err(e)
        return EXIT_ERROR
    print(json.dumps({
        "pack": lock["pack"],
        "version": lock.get("version", "unknown"),
        "target": lock["target"],
        "base_url": lock.get("base_url", DEFAULT_BASE_URL),
        "count": len(lock["skills"]),
        "slugs": sorted(lock["skills"]),
    }, sort_keys=True, indent=2))
    return EXIT_OK


def cmd_lock_check(args):
    lock, e = load_lock(Path(args.file).expanduser())
    if e:
        err(e)
        return EXIT_ERROR
    staging = Path(args.staging).expanduser()
    if not (staging / "skills").is_dir():
        err(f"staging dir has no skills/ directory: {staging}")
        return EXIT_ERROR
    drift = []
    for slug, h in sorted(lock["skills"].items()):
        p = staging / "skills" / f"{slug}.md"
        if not p.exists():
            drift.append({"slug": slug, "kind": "missing", "path": str(p),
                          "expected": h, "actual": None})
            continue
        try:
            actual = sha256_file(p)
        except OSError:
            drift.append({"slug": slug, "kind": "unreadable", "path": str(p),
                          "expected": h, "actual": None})
            continue
        if actual != h:
            drift.append({"slug": slug, "kind": "modified", "path": str(p),
                          "expected": h, "actual": actual})
    staging_slugs = sorted(f.stem for f in (staging / "skills").glob("*.md")
                           if SLUG_RE.match(f.stem))
    full_match = set(lock["skills"]) == set(staging_slugs)
    result = {
        "drift": drift,
        "locked_count": len(lock["skills"]),
        "staging_count": len(staging_slugs),
        "full_match": full_match,
    }
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    elif drift:
        print(f"{RED}lock drift:{RST} catalog content no longer matches {args.file}")
        for d in drift:
            print(f"  [{d['kind']}] {d['slug']}")
            if d["kind"] == "modified":
                print(f"    expected: {short_hash(d['expected'])}")
                print(f"    actual:   {short_hash(d['actual'])}")
        print("review changes with: usb diff <slug>   re-pin with: usb lock")
    else:
        print(f"{GRN}ok:{RST} staging matches the lock "
              f"({len(lock['skills'])} skill(s))")
    return EXIT_DRIFT if drift else EXIT_OK


# --- diff -------------------------------------------------------------------

def load_secscan():
    path = Path(__file__).resolve().parent / "usb-secscan.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("usb_secscan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_skill_path(raw):
    p = Path(raw).expanduser()
    if p.is_dir():
        cand = p / "SKILL.md"
        if cand.is_file():
            return cand
        mds = sorted(p.glob("*.md"))
        if len(mds) == 1:
            return mds[0]
        raise ValueError(f"{p}: directory needs a SKILL.md or exactly one .md")
    if p.is_file():
        return p
    raise ValueError(f"path does not exist: {p}")


def analyze_version(secscan, path: Path):
    unit = secscan.build_unit("local", str(path.parent), path.name, path)
    if unit is None or unit == "binary":
        raise ValueError(f"cannot analyze (unreadable or binary): {path}")
    structural, _ = secscan.analyze_structure(unit, {})
    art = secscan.build_adapter_result(unit)
    observed_counts = {}
    for e in art["observed_capabilities"]:
        observed_counts[e["category"]] = observed_counts.get(e["category"], 0) + 1
    declared = sorted(art["declared"]["categories"])
    art["undeclared_categories"] = sorted(set(observed_counts) - set(declared))
    contract = secscan.p101_findings(art)
    contract.extend(secscan.p102_finding(art))
    verdict = secscan.compute_verdict(structural, contract)
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "verdict": verdict,
        "frontmatter": {k: v["value"] for k, v in sorted(unit.fm["meta"].items())},
        "tools": sorted(t["name"] for t in art["declared"]["tools"]),
        "observed": dict(sorted(observed_counts.items())),
        "declared": declared,
    }


def diff_versions(old, new):
    fm_changes = []
    for key in sorted(set(old["frontmatter"]) | set(new["frontmatter"])):
        a = old["frontmatter"].get(key)
        b = new["frontmatter"].get(key)
        if a != b:
            fm_changes.append({"field": key, "old": a, "new": b})
    old_obs, new_obs = set(old["observed"]), set(new["observed"])
    old_dec, new_dec = set(old["declared"]), set(new["declared"])
    old_tools, new_tools = set(old["tools"]), set(new["tools"])
    return {
        "identical": old["sha256"] == new["sha256"],
        "verdict_changed": old["verdict"] != new["verdict"],
        "frontmatter_changes": fm_changes,
        "capabilities": {
            "observed_added": sorted(new_obs - old_obs),
            "observed_removed": sorted(old_obs - new_obs),
            "declared_added": sorted(new_dec - old_dec),
            "declared_removed": sorted(old_dec - new_dec),
            "tools_added": sorted(new_tools - old_tools),
            "tools_removed": sorted(old_tools - new_tools),
        },
    }


def cmd_diff(args):
    try:
        old_path = resolve_skill_path(args.old)
        new_path = resolve_skill_path(args.new)
    except ValueError as e:
        err(str(e))
        return EXIT_ERROR
    secscan = load_secscan()
    if secscan is None:
        err("usb-secscan.py must sit next to usb-integrity.py for capability analysis")
        return EXIT_ERROR
    try:
        old = analyze_version(secscan, old_path)
        new = analyze_version(secscan, new_path)
    except ValueError as e:
        err(str(e))
        return EXIT_ERROR
    result = diff_versions(old, new)
    patch = None
    if args.patch:
        old_lines = old_path.read_text(encoding="utf-8", errors="replace").splitlines()
        new_lines = new_path.read_text(encoding="utf-8", errors="replace").splitlines()
        patch = list(difflib.unified_diff(
            old_lines, new_lines, fromfile=str(old_path), tofile=str(new_path),
            lineterm=""))[:PATCH_MAX_LINES]
    if args.json:
        payload = {"tool": TOOL, "version": VERSION, "old": old, "new": new,
                   **result}
        if patch is not None:
            payload["patch"] = patch
        print(json.dumps(payload, sort_keys=True, indent=2))
    else:
        human_diff(old, new, result, patch)
    return EXIT_OK if result["identical"] else EXIT_DRIFT


def human_diff(old, new, result, patch):
    print("USB Skill Diff")
    print(f"old: {old['path']}")
    print(f"     {short_hash(old['sha256'])}  verdict: {old['verdict']}")
    print(f"new: {new['path']}")
    print(f"     {short_hash(new['sha256'])}  verdict: {new['verdict']}")
    print()
    if result["identical"]:
        print(f"{GRN}identical{RST} — no changes")
        return
    if result["verdict_changed"]:
        print(f"{YEL}verdict:{RST} {old['verdict']} -> {new['verdict']}")
    if result["frontmatter_changes"]:
        print("Frontmatter changes:")
        for c in result["frontmatter_changes"]:
            if c["old"] is None:
                print(f"  + {c['field']}: {c['new']}")
            elif c["new"] is None:
                print(f"  - {c['field']}: {c['old']}")
            else:
                print(f"  ~ {c['field']}: {c['old']} -> {c['new']}")
    caps = result["capabilities"]
    if any(caps.values()):
        print("Capability changes:")
        for cat in caps["observed_added"]:
            print(f"  {RED}+ observed: {cat}{RST} "
                  f"({new['observed'][cat]} evidence line(s))")
        for cat in caps["observed_removed"]:
            print(f"  {GRN}- observed: {cat}{RST}")
        for cat in caps["declared_added"]:
            print(f"  + declared: {cat}")
        for cat in caps["declared_removed"]:
            print(f"  - declared: {cat}")
        if caps["tools_added"]:
            print(f"  {RED}+ tools: {', '.join(caps['tools_added'])}{RST}")
        if caps["tools_removed"]:
            print(f"  {GRN}- tools: {', '.join(caps['tools_removed'])}{RST}")
    if not result["verdict_changed"] and not result["frontmatter_changes"] \
            and not any(caps.values()):
        print("content changed (hashes differ) but no capability or")
        print("frontmatter changes detected")
    if patch:
        print()
        print("Patch:")
        for line in patch:
            print(f"  {line}")


# --- entry point -------------------------------------------------------------

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="usb-integrity",
        description="Integrity verification, version locking and capability "
                    "diffs for installed USB skills.",
        epilog="exit codes: 0 clean/identical, 1 drift/differences, 2 error")
    parser.add_argument("--version", action="version",
                        version=f"{TOOL} {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("verify", help="verify installed files against the install manifest")
    p.add_argument("slug", nargs="?", help="verify one skill instead of all")
    p.add_argument("--all", action="store_true", help="verify everything (default)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--target", help="restrict to one runtime target")
    p.add_argument("--pack-dir", help="verify a specific pack directory")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("lock", help="pin installed skills into a lock file")
    p.add_argument("--file", default=LOCK_NAME)
    p.add_argument("--target", help="override the recorded target")
    p.add_argument("--pack-dir")
    p.set_defaults(fn=cmd_lock)

    p = sub.add_parser("lock-show", help="print a validated lock file as JSON")
    p.add_argument("--file", default=LOCK_NAME)
    p.set_defaults(fn=cmd_lock_show)

    p = sub.add_parser("lock-check", help="compare a staging pack dir against a lock")
    p.add_argument("--staging", required=True)
    p.add_argument("--file", default=LOCK_NAME)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_lock_check)

    p = sub.add_parser("diff", help="capability/permission diff of two skill versions")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--json", action="store_true")
    p.add_argument("--patch", action="store_true", help="include a unified diff")
    p.set_defaults(fn=cmd_diff)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())