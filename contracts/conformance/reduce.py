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

    rules = {f["rule"] for f in structural} | {f["rule"] for f in contract}
    # S002 (broken frontmatter) and S101 (broken MCP config) both mean: no
    # contract can even be read.
    if "S002" in rules or "S101" in rules:
        verdict = "unverifiable"
    elif any(f["rule"] == "P101" and f["severity"] in ("high", "medium")
             for f in contract):
        verdict = "purpose-mismatch"
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
