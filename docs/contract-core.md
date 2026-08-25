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

### 4.1 Verdict ≠ policy

The core produces verdicts; enforcement is a separate policy layer.

| verdict | default policy |
|---|---|
| `purpose-mismatch` | **BLOCKING** |
| `excessive-authority` | **ADVISORY** |
| `unverifiable` | **CONTEXTUAL** |

Rationale: excessive authority alone is not malice — a backup agent
legitimately requests broad file access. A CI owner may promote advisory
to blocking through policy configuration; the vocabulary itself never
bends to enforcement preferences. Today's CLI implements the default
policy inline; when the core lands, policy becomes explicit input.

## 5. Canonical form & fingerprints

Findings serialize to a canonical JSON shape (sorted keys, fixed field
order, UTF-8). Fingerprints hash the canonical bytes — currently
`sha256(rule + "\0" + quote)[:16]`. If the canonical shape changes, the
fingerprint version bumps and old baselines are invalid by definition.
Cross-language implementations converge by asserting byte-equality against
shared golden fixtures (§7).

## 6. Resolved decisions

The three foundational questions, locked before any core code exists:

1. **Spec + conformance, not a package.** The source of truth is this
   document plus golden fixtures under `contracts/fixtures/`; every
   implementation — the CLI engine today, a future TS core tomorrow —
   must pass `contracts/conformance/`. No `@usb/contract-core` package
   ships until an implementation exists that passes the suite.
2. **Verdict ≠ policy** (see §4.1): purpose-mismatch blocks by default,
   excessive-authority is advisory, unverifiable is contextual. Core and
   enforcement stay separate.
3. **MCP depth order**, each stage gated on the previous being green:
   ```
   config → declared server/tools → tool graph → implementation source
          → runtime observation
   ```
   This matches USB's existing philosophy end to end: what it says it is,
   what it connects to, which tools it serves.

## 7. Conformance suite

Golden corpus = fixture trees (already exist for skills) + expected
canonical JSON snapshots under `contracts/fixtures/`. The runner lives in
`contracts/conformance/`; CI asserts byte-equality per implementation.
Adding a rule means updating goldens deliberately — visible in review,
never accidental.

## 8. Migration path

**Step 1 — artifact extraction (behavior-preserving):**

1. Emit the four contract artifacts from the *current* engine internals
   without changing any observable behavior.
2. Snapshot them as golden fixtures (`contracts/fixtures/`) and diff
   old-vs-new output until byte-identical.
3. Only then draw the adapter boundaries the artifacts reveal.
4. Extract the shared core last — it must pass the conformance suite or
   it does not ship.

Then: MCP adapter on the core (config layer) → agent/command adapters →
fingerprints on the stabilized canonical form.

