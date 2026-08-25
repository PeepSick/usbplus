# USB Contract Core — Design Spec

Status: **design, pre-implementation** (Trust Layer phase 4).
This document is the contract for `@usb/contract-core`: the shared,
deterministic decision engine that every capability-format adapter feeds.

Nothing here is implemented yet. Change anything by opening a PR; the doc
is the source of truth until code exists, after which this file and the
code must move together.

---

## 1. Problem

Today the verification logic (declared-vs-observed reduction, severity
tiering, verdict precedence, fingerprinting) lives inside the skill
scanner's engine. Every new format (MCP configs, agent definitions,
plugins) would either duplicate that logic or invent its own vocabulary.
The core extracts it once, so formats differ only in *discovery and
normalization* — never in how a verdict is reached.

## 2. Positioning

```
USB scans repositories for AI capabilities
    ↓ discovery (format-specific adapters)
AdapterResult ×N
    ↓ contract core (pure functions)
Verdict + canonical findings
```

USB does not say "safe" or "unsafe". The fixed verdict vocabulary is:

| verdict | meaning |
|---|---|
| `matches-declaration` | everything observed is covered by the declaration |
| `purpose-mismatch` | an observed capability was never declared |
| `excessive-authority` | requested authority is out of proportion to the purpose |
| `unverifiable` | the claim cannot be parsed, so no contract can be read |

## 3. Adapter interface — the four artifacts

Every adapter consumes its format's raw sources and emits exactly:

```ts
interface AdapterResult {
  identity: {
    name: string;            // resolved name (frontmatter/dir/file/config key)
    kind: "skill" | "mcp-server" | "agent" | "command" | "plugin";
    source: SourceRef;       // stable, path-independent locator(s)
  };
  declared: DeclaredContract;   // what the unit SAYS it does
  observed: CapabilityEvidence[]; // what it ACTUALLY reaches for
  authority: AuthorityScope;      // what reach it REQUESTS
}
```

- **DeclaredContract** — free-text fields (`name`, `description`,
  trigger/when-to-use, tags) plus any structured claims the format allows
  (e.g. an MCP entry's inline tool list).
- **CapabilityEvidence** — one normalized capability category per entry:
  `{ category, strength: "strong"|"weak", source: SourceRef, line, text }`.
  v1 taxonomy stays aligned with today's engine categories so existing
  rule IDs remain stable: `secrets-access`, `network-egress`,
  `destructive-commands`, `process-execution`, `persistence-hooks`.
  New categories may be added; renaming/removing is a breaking change.
- **Evidence** — file:line anchored quotes, trimmed deterministically,
  capped per rule (first N by line). This is the audit trail.
- **AuthorityScope** — the *requested* reach even when never exercised:
  mounts/globs, remote transports, environment variables, permission
  bypass flags. Feeds `excessive-authority`; today's A-rules are its
  precursor signals.

**Adapter rules:** pure functions of their inputs; read-only filesystem
access; sorted walks and stable sorts only; no clock, randomness, locale
or network. Anything nondeterministic is disqualified.

## 4. Core reduction — pure functions

```
verify(result) → { verdict, findings[], summary }
```

1. `undeclared = observed.categories − declared.categories`
2. Each undeclared category → finding `P101`, severity = category base,
   downgraded one level when only weak evidence supports it (never below low).
3. Authority check: scope requests sensitive reach whose domain words are
   absent from the declared text → `excessive-authority` input
   (corroboration rules defined in §6).
4. Verdict precedence: `unverifiable` → `purpose-mismatch` →
   `excessive-authority` → `structural-issues` → `matches-declaration`.
5. Exit mapping (CLI): 0 clean/low/info, 1 medium/high, 2 operational error.
   Baselines filter on fingerprints before gating.

## 5. Canonical form & fingerprints

Findings serialize to a canonical JSON shape (sorted keys, fixed field
order, UTF-8). Fingerprints hash the canonical bytes — currently
`sha256(rule + "\0" + quote)[:16]`. If the canonical shape changes, the
fingerprint version bumps and old baselines are invalid by definition.
Cross-language implementations converge by asserting byte-equality against
shared golden fixtures (§7).

## 6. Open decisions (need answers before coding)

1. **Implementation language & packaging.** Options:
   - (a) TS-first: `packages/contract-core` (zero-dep), consumed by SDK +
     catalog API; CLI keeps a Python port.
   - (b) Python-first: engine module published/vendored into the CLI.
   - (c) Spec + conformance: no shared runtime; golden-fixture suite that
     every implementation must match byte-for-byte.
   Recommendation: start with (c) plus a single reference implementation
   in the CLI, promote to (a) when the catalog needs server-side
   verdicts. Avoids dual-runtime drift while fingerprints stay the real
   contract.
2. **Is `excessive-authority` blocking?** Today authority language is
   advisory-only. Proposal: v1 keeps it non-blocking unless corroborated
   by strong evidence or co-occurring with a P101; revisit with real data.
3. **MCP depth order:** config-layer evidence → inline tool manifests →
   implementation-source adapters (Node, then Python). Sequencing affects
   nothing else in the core.

## 7. Conformance suite

Golden corpus = fixture trees (already exist for skills) + expected
canonical JSON snapshots. CI asserts byte-equality per implementation.
Adding a rule means updating goldens deliberately — visible in review,
never accidental.

## 8. Migration path

1. Extract pure functions from `usb-secscan.py`
   (observe / declare / reduce / fingerprint) behind the interfaces above;
   behavior-preserving, proven by the existing suite byte-for-byte.
2. Split adapters out of the monolith (skill first).
3. Land MCP adapter on the core (config layer) against the OpenWork corpus.
4. Promote agent/command to their own adapters.
5. Fingerprints (phase 5) build on the canonical form only after step 1–3
   stabilize.
