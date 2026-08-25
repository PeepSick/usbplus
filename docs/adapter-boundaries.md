# Adapter Boundaries — Migration Step 1 Findings

Status: proposal from Step 1 (artifact extraction). The conformance suite
proves the four artifacts carry **all** information needed to re-derive
every finding and verdict — which means the boundary below is real, not
aspirational. Shared-core extraction is now mechanical.

---

## What Step 1 proved

`--emit-contracts` serializes, per unit: identity, declared contract,
observed capabilities (with strength), evidence by rule, authority scope,
undeclared categories, structural findings, and verdict. The independent
reducer (`contracts/conformance/reduce.py`) re-derives the complete
contract-findings set + verdict from those artifacts alone and matches the
engine semantically on the fixture corpus (12 units, byte-deterministic).

## Discovered boundary

```
┌─────────────────────────── ADAPTER ────────────────────────────┐
│ discovery        location rules, storage dirs, config shapes   │
│ normalization    raw file → Unit (frontmatter subset, names)   │
│ declared         claimed_purpose_text + declared categories    │
│ observed         prose patterns + bundled-script patterns      │
│ evidence         per-rule {line, quote, source} entries        │
│ authority        A-rule signals + format-specific scope        │
└────────────────────────────┬───────────────────────────────────┘
                             ↓ AdapterResult
┌──────────────────────────── CORE ──────────────────────────────┐
│ undeclared = observed − declared                               │
│ severity = category base − weak-only downgrade                 │
│ corroboration = signals materialize iff P101 present           │
│ verdict precedence (unverifiable > purpose-mismatch > …)       │
│ fingerprints (rule+quote), caps, baseline filtering            │
└────────────────────────────┬───────────────────────────────────┘
                             ↓ presentation (--json / human)
```

## Function → adapter mapping (current engine)

| today (usb-secscan.py) | becomes |
|---|---|
| `walk_candidates`, `is_card_by_location`, `definition_format` | skill adapter discovery |
| `parse_frontmatter`, `build_unit` | skill adapter normalization |
| `collect_bundle_scripts` | skill adapter observation source |
| `observe_capabilities` (prose+scripts) | adapter.observed |
| `declared_categories`, `claimed_purpose_text` | adapter.declared |
| `collect_authority_signals` | adapter.authority |
| `analyze_structure` | adapter structural layer (stays adapter-side) |
| `p101_findings`, `p102_finding`, `_downgrade`, corroboration | **core** |
| `compute_verdict`, `cap_per_rule`, fingerprinting | **core** |
| baseline load/write/filter | **core** (policy-adjacent, format-free) |
| `human_report`, JSON rendering | presentation |

Step 2 landed: the core consumes AdapterResult directly
(`build_adapter_result` is the single adapter→core seam; P-rules read
artifacts, not files).

## Rules extracted for the MCP adapter

1. An MCP unit needs only: identity, claim text, transport/command/env
   observations, authority flags. No frontmatter, no bundle walking.
2. Observation sources differ; the artifact schema does not.
3. Structural rules stay with the adapter (S1xx), contract rules stay in
   the core (P/A).
