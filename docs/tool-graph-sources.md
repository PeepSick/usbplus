# Tool Graph Sources — Stage 3 Design Proposal

Status: **design discussion, pre-implementation** (Trust Layer depth stage 3).
This document analyzes ONE question before any code exists:

> From which STATIC sources can an MCP server's tool graph be extracted
> reliably — without running the server and without touching a network?

USB's determinism contract ("never executes what it scans", byte-identical
reports for identical trees) is the constraint everything below is tested
against.

---

## 1. Source inventory

| # | Source | Deterministic? | Reliable? | In scope for stage 3? |
|---|---|---|---|---|
| S1 | Config-declared tools (`tools` arrays, Cursor `mcpAllowlist`) | yes | yes (it IS a declaration) | ✅ already landed (v2.5) |
| S2 | Implementation source **inside the scanned tree** (official-SDK registration idioms) | yes | medium-high | 🟡 proposed core of stage 3 |
| S3 | Package references (`npx -y @scope/pkg`, `uvx pkg`) resolved via registry/lockfile | registry fetch: **no** (time-variant); lockfile read: yes but yields identity, not tools | n/a | ❌ static path: marked **opaque** |
| S4 | Schema sidecars in-tree (OpenAPI specs wrapped into tools by known frameworks) | yes | low-medium (framework-dependent mapping) | ⏸ later refinement of S2 |
| S5 | Runtime observation (launch → initialize → `tools/list`) | **no** (process + network + server state) | highest (ground truth) | ❌ never inside `secscan`; possible future opt-in mode, clearly labeled non-deterministic |

## 2. The decisive split: S2 is evidence, not declaration

Statically parsing a server's source and finding `@mcp.tool` /
`server.tool("name", ...)` registrations answers "what does this program
REGISTER to serve" — that is **observed behavior surface** at the code
level, exactly analogous to how bundled scripts are already behavior
evidence for skills. It must therefore enter the artifacts as
**CapabilityEvidence** (category derived from tool name/description via the
existing lexicon, file:line anchored), feeding P101 like any other observed
capability — never as `declared`.

This keeps the existing chain intact:

```
declared (config/name/tools)  ←  unchanged
observed  (+ tool registrations found in source)
authority (+ opaque-package notes)
P101 / P103 semantics      ←  unchanged
```

No new verdict, no new rule ID required for extraction itself; only the
adapter gains a deeper observation source. If a category taxonomy extension
ever becomes necessary (e.g. `tool-serving`), that is a deliberate,
golden-breaking spec change on its own.

## 3. The completeness problem (and why we do not solve it)

Static parsing can miss dynamically registered tools (metaprogramming,
re-exports, config-driven loops). Therefore stage 3 NEVER claims "this is
the full tool graph". It claims only:

> "these registrations were found, each anchored at file:line"

Absence of evidence remains non-evidence — P101 already behaves correctly
under that asymmetry (it flags what IS found undeclared; silence adds
nothing). Any UI wording must say "found tool registrations", never "the
server exposes N tools".

## 4. The availability gate

S2 applies ONLY when the entry's launch line resolves INSIDE the scanned
tree (relative paths, `node ./server.js`, `python -m pkg.server` where the
package dir is in-tree). Package-referencing servers (`npx`, `uvx`, bare
binaries) are recorded in `authority_scope` as an explicit **opaque**
marker — a real security fact: their tool surface cannot be reviewed from
this tree at all. Fetching packages to parse them would introduce network,
registry state and time variance; that is S5 territory or a future pinned,
cached mode — never the default scan.

## 5. Reliability of S2 itself

Official SDKs dominate real servers and their registration idioms are
regular:

- Python: `FastMCP` `@mcp.tool()` decorators (name from function or kwarg,
  description from docstring), `@server.list_tools()` handlers
- TypeScript: `server.tool("name", schema, handler)`,
  `registerTool({...})`

Plan: idiom parsers ship WITH published coverage limits documented per SDK
version; every extracted registration carries its anchor so reviewers can
verify; unknown-code paths simply produce nothing (no guessing). Conformance
extends the acme-agent corpus with tiny fixture servers whose registrations
are hand-counted; goldens lock the extraction byte-for-byte, same as
everywhere else.

## 6. What stage 3 deliberately does NOT do

- No network, no process launches, no registry fetches.
- No completeness/graph-isomorphism claims.
- No mixing of S5 results into deterministic reports (a future
  `usb mcprobe`-style opt-in would live outside secscan entirely and label
  its output non-deterministic).
- No changes to verdict vocabulary, severity ladder or reducer logic.

## 7. Resolved decisions

Locked before any extraction code exists (conformance-first culture):

1. **v1 SDK scope: Python AND TypeScript, official SDK idioms only.**
   Python: `FastMCP` `@mcp.tool()` decorators. TypeScript:
   `server.tool("name", ...)`, `registerTool({...})`. Rationale: a large
   share of real servers — including the official reference servers — are
   TypeScript; shipping Python-only would blind the feature at birth.
   Per-idiom coverage limits are documented in code and expand
   deliberately, never silently.
2. **Tool names AND descriptions feed the capability lexicon**, anchored
   at the registration site. Code string literals are evidence under the
   same philosophy that treats bundled scripts as behavior proof; names
   alone would miss the plainest admissions ("uploads reports over http").
3. **Unresolvable package references serialize as a NEW key,
   `authority_scope.opaque`** — sorted, verbatim launch-line specs the
   static layer could not resolve inside the tree. Reducers ignore unknown
   keys, so this is additive; transports stay pure transport types.
4. **Conformance fixture lives in a second tree,
   `contracts/fixtures/e2e/toolgraph/`**: miniature Python + TypeScript
   servers with hand-counted registrations, an `.mcp.json` referencing them
   via relative paths (exercises the availability gate) and one
   package-referencing entry (asserts the opaque marker). `run_e2e.py`
   scans both corpus roots in one pass; goldens remain combined and
   path-normalized.

With these closed, stage 3's design boundary is set: static, in-tree,
anchored, additive to the existing artifacts — everything else stays on
the far side of the line until a separate, explicitly non-deterministic
mode is designed for it.

## 8. Build order — corpus first, parser last

Implementation enters the same conformance-first gate as everything else:

1. Build `contracts/fixtures/e2e/toolgraph/` (mini Python + TypeScript
   servers, a relative-path launch entry exercising the availability gate,
   an npx entry asserting the opaque marker).
2. Hand-write the EXPECTED evidence/artifact results — which registration
   maps to which category at which file:line anchor. That expectation is
   the golden target.
3. Extend `run_e2e.py` to scan both corpus roots; it must fail against the
   missing parser exactly as predicted (this validates the runner too).
4. Lock goldens deliberately (`--update`).
5. Only then write the idiom parsers, against that fixed target.
6. Registrations flow into artifacts as CapabilityEvidence; P101/P103
   semantics and reducer logic remain untouched.

Principle: evidence contract first, code second.
