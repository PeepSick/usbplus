#!/usr/bin/env python3
"""Independent reducer for usb-contract-artifacts/v1.

Re-derives findings and verdicts from the four contract artifacts ALONE,
implementing docs/contract-core.md §4. This file deliberately does NOT
import the secscan engine: the duplication of the category tables is the
point. If the engine and this reducer ever disagree, either an adapter is
losing information on the way into the artifacts, or the spec and the
engine have drifted - both are conformance failures.

Comparison scope (Migration Step 1): findings are compared on their
canonical fields (rule, severity, line, quote, evidence_path, fingerprint).
Explanatory prose (`detail`) and run-scoped policy state are excluded.
"""

import hashlib
import json
import re

SCHEMA = "usb-contract-artifacts/v1"
LADDER = ["info", "low", "medium", "high"]

# Independent copy of the category base severities (spec §3 taxonomy v1).
CATEGORY_BASE = {
    "secrets-access": "high",
    "network-egress": "high",
    "destructive-commands": "medium",
    "process-execution": "medium",
    "persistence-hooks": "high",
}

# Independent copy of P103 vocabulary tables (engine: MCP_GENERIC_*).
_P103_CREDENTIAL_RE = re.compile(
    r"(token|secret|password|passwd|private[_-]?key|api[_-]?key|credential)",
    re.IGNORECASE)
_P103_FILESYSTEM_RE = re.compile(
    r"file|folder|director|storage|backup|sync|mount|fs", re.IGNORECASE)
_P103_GENERIC_ITEMS = frozenset((
    "token", "secret", "password", "passwd", "key", "api", "auth",
    "credential", "credentials", "var", "env", "prod", "production",
    "staging", "dev", "test", "the"))
_P103_GENERIC_PATHS = frozenset((
    "home", "users", "user", "opt", "usr", "local", "bin", "sbin", "tmp",
    "etc", "var", "lib", "app", "data", "srv", "mnt", "media", "windows",
    "program", "files", "x86", "js", "mjs", "cjs", "ts", "py", "sh",
    "json", "yaml", "yml", "md", "exe", "dll", "com"))


def _p103_unexplained(declared_text, authority_scope):
    """Mirror of the engine's p103_finding item selection. Returns the
    unexplained-item labels in the same deterministic order."""
    claim_lower = declared_text.lower()

    def tokens(item):
        return [p.lower() for p in re.split(r"[^A-Za-z0-9]+", item)
                if len(p) >= 3]

    def explained(toks):
        if not toks:
            return True
        return any(t in claim_lower for t in toks)

    out = []
    for key in authority_scope.get("env_keys") or []:
        if not _P103_CREDENTIAL_RE.search(key):
            continue
        toks = [t for t in tokens(key) if t not in _P103_GENERIC_ITEMS]
        if not explained(toks):
            out.append(f"env {key}")
    for mount in authority_scope.get("mounts") or []:
        if _P103_FILESYSTEM_RE.search(claim_lower):
            break  # a filesystem-flavored purpose explains path mounts
        segs = [t for t in tokens(mount) if t not in _P103_GENERIC_PATHS]
        if not explained(segs):
            out.append(f"mount {mount}")
    return out


def fingerprint(rule: str, quote: str) -> str:
    return hashlib.sha256(
        f"{rule}\x00{quote}".encode("utf-8")).hexdigest()[:16]


def _downgrade(severity: str) -> str:
    return LADDER[max(LADDER.index(severity) - 1, 1)]


def reduce_unit(unit: dict):
    """-> (contract_findings, verdict). Structural findings pass through."""
    structural = list(unit.get("structural_findings", []))
    declared = set(unit["declared"]["categories"])

    observed = {}
    for e in unit.get("observed_capabilities", []):
        observed.setdefault(e["category"], []).append(e)

    contract = []
    undeclared = sorted(set(observed) - declared)
    for category in undeclared:
        evs = sorted(observed[category],
                     key=lambda e: (e.get("source") or "", e["line"]))
        any_strong = any(e["strength"] == "strong" for e in evs)
        severity = CATEGORY_BASE[category] if any_strong \
            else _downgrade(CATEGORY_BASE[category])
        first = evs[0]
        f = {
            "rule": "P101",
            "severity": severity,
            "title": f"undeclared capability: {category}",
            "line": first["line"],
            "quote": first["text"],
            "fingerprint": fingerprint("P101", first["text"]),
        }
        if first.get("source"):
            f["evidence_path"] = first["source"]
        contract.append(f)

    # Corroborated authority signals materialize only next to a P101.
    # Title template matches the engine's canonical rendering.
    if any(f["rule"] == "P101" for f in contract):
        for s in unit.get("authority_scope", {}).get("signals", []):
            contract.append({
                "rule": s["rule"],
                "severity": "medium",
                "title": ("authority signal corroborating undeclared "
                          f"capability: {s['title']}"),
                "line": s["line"],
                "quote": s["quote"],
                "fingerprint": fingerprint(s["rule"], s["quote"]),
                "corroborated": True,
            })

    # P103: unexplained authority breadth - advisory (always low), one
    # aggregated finding, anchored at line 1 on both sides.
    unexplained = _p103_unexplained(unit["declared"].get("text", ""),
                                    unit.get("authority_scope", {}))
    if unexplained:
        # mirror of the engine's trim_quote
        quote = " ".join(unexplained[0].split())
        if len(quote) > 200:
            quote = quote[:197] + "..."
        contract.append({
            "rule": "P103",
            "severity": "low",
            "title": "authority beyond declared purpose",
            "line": 1,
            "quote": quote,
            "fingerprint": fingerprint("P103", quote),
        })

    rules = {f["rule"] for f in structural} | {f["rule"] for f in contract}
    # S002 (broken frontmatter), S101 (broken MCP config) and S102 (unreadable
    # declared tool list) all mean: the relevant contract cannot be read.
    if {"S002", "S101", "S102"} & rules:
        verdict = "unverifiable"
    elif any(f["rule"] == "P101" and f["severity"] in ("high", "medium")
             for f in contract):
        verdict = "purpose-mismatch"
    elif any(f["rule"] == "P103" for f in contract):
        # advisory by construction: P103 is always low, so this sits BELOW
        # purpose-mismatch and never blocks
        verdict = "excessive-authority"
    elif rules:
        verdict = "structural-issues"
    else:
        verdict = "matches-declaration"

    return contract, verdict


def canonical(f: dict) -> dict:
    out = {k: f[k] for k in ("rule", "severity", "title", "line", "quote")
           if k in f}
    if "fingerprint" in f:
        out["fingerprint"] = f["fingerprint"]
    if f.get("evidence_path"):
        out["evidence_path"] = f["evidence_path"]
    if f.get("corroborated"):
        out["corroborated"] = True
    return out


def compare_unit(engine_unit: dict, artifacts_unit: dict):
    """-> list of semantic mismatch strings for one unit.

    The engine report carries a single flat findings list; S-rules form its
    structural layer, everything else is contract output."""
    problems = []
    contract, verdict = reduce_unit(artifacts_unit)
    all_engine = engine_unit.get("findings", [])
    structural = [canonical(f) for f in all_engine
                  if f["rule"].startswith("S")]
    actual_contract = [canonical(f) for f in all_engine
                       if not f["rule"].startswith("S")]

    def keyset(items):
        return sorted(json.dumps(i, sort_keys=True) for i in items)

    label = engine_unit["source"]["path"]
    if keyset(structural) != keyset(artifacts_unit.get("structural_findings", [])):
        problems.append(f"{label}: structural passthrough drifted")
    if keyset(actual_contract) != keyset([canonical(f) for f in contract]):
        only_old = [f for f in actual_contract
                    if json.dumps(canonical(f), sort_keys=True)
                    not in set(keyset([canonical(x) for x in contract]))]
        only_new = [f for f in contract
                    if json.dumps(canonical(f), sort_keys=True)
                    not in set(keyset([canonical(x) for x in actual_contract]))]
        problems.append(
            f"{label}: contract findings differ "
            f"(old-only={only_old} new-only={only_new})")
    if engine_unit.get("verdict") != verdict:
        problems.append(f"{label}: verdict {unit.get('verdict')} vs "
                        f"reduced {verdict}")
    return problems


def check_schema(doc: dict):
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"unexpected artifact schema {doc.get('schema')!r}")
