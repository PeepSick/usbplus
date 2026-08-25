# usb mcprobe — Runtime Observation Design Proposal

Status: **design, pre-implementation** (Trust Layer depth stage: runtime
observation). Companion to docs/tool-graph-sources.md, which drew the line
this document formalizes:

> Static analysis (secscan) never executes what it scans.
> Runtime observation (mcprobe) always does — explicitly, opt-in, and
> clearly labeled non-deterministic.

---

## 1. Problem

Stage 2 reads what a server DECLARES; stage 3 reads what its source
REGISTERS when the source is inside the tree. Neither answers the final
question: **what tools does the server actually expose at runtime?** Only
the MCP handshake can answer that. The answer is valuable enough to
justify a dedicated tool — but it must never leak into the deterministic
audit chain, because it cannot be reproducible.

## 2. Positioning — a separate mode, not a flag

| | secscan | mcprobe |
|---|---|---|
| executes targets | never | yes (spawn / network) |
| network | never | yes |
| deterministic output | byte-identical, goldens | non-deterministic by definition |
| CI gating | exit codes gate | advisory data only |
| default behavior | runs implicitly on paths | never implicit |

mcprobe reuses secscan's DISCOVERY (config parsing, adapter identity,
locators) but shares no execution path and writes nothing into
`usb-contract-artifacts`. A secscan report can never contain an
observation made by mcprobe.

## 3. Safety model (the core of the design)

Launching an untrusted MCP server means running untrusted code — the
exact act secscan exists to avoid. Therefore:

1. **Never implicit.** mcprobe only ever runs as its own command, per
   target. Without `--yes` it prints exactly what it WOULD do (command,
   transport, contact class, risk boundary) and stops. `--yes` is
   explicit machine consent; piped stdin counts as REFUSAL — faking an
   interactive confirmation is not allowed, and `--yes` already covers
   automation honestly.
2. **No tool calls. Ever.** Protocol scope is exactly: `initialize` →
   `initialized` → `tools/list` → shutdown. Registration of capabilities
   is observed; invocation is out of scope permanently.
3. **Bounded contact.** Hard timeout on every phase; SIGTERM then kill;
   output size caps. A hung server is a finding, not a crash.
4. **Environment minimization + redaction.** The server gets the env vars
   its config entry declares — nothing inherited unless explicitly
   requested. The REPORT never contains secret-bearing values: env values,
   header values and the launch line are redacted to key names /
   placeholder forms before anything is written.
5. **Remote entries are safer than local ones** (no process spawn); the
   report records which contact class was used so consumers can weigh it.

## 4. Protocol flow (stdio)

```
spawn(command, args, env=entry.env)      # cwd: config dir
→ initialize (protocolVersion, capabilities, serverInfo)
→ initialized notification
→ tools/list (+ pagination cursor loop, capped)
→ graceful shutdown: close stdin → wait(brief) → SIGTERM → kill
```

HTTP/SSE transports skip the spawn and reuse the same message sequence
over the wire. Every phase transition is recorded with ok/error/timeout so
a partial probe is still an honest report.

Pagination is bounded on both axes: default **10 pages**, hard ceiling of
**1000 tools**. A repeated cursor stops the loop immediately; an empty or
malformed page is recorded as a protocol failure. Reaching either bound
marks the observation PARTIAL — never "zero tools":

```json
"observation": { "complete": false, "reason": "pagination_limit" }
```

## 5. Output contract — `usb-mcprobe-report/v1`

```json
{
  "schema": "usb-mcprobe-report/v1",
  "mode": "runtime-observation",
  "deterministic": false,
  "target": { "locator": "...", "transport": "stdio", "contact": "local-process" },
  "launch_line": "<redacted>",
  "handshake": { "initialize": "ok", "tools_list": "ok" },
  "server_info": { "name": "...", "version": "..." },
  "observation": { "complete": true, "reason": null },
  "tools": [ { "name": "...", "description": "...", "input_schema_keys": [] } ]
}
```

- `tools` is sorted by name; `input_schema_keys` keeps only top-level
  property names (bounded review signal, no schema dumps).
- `launch_line` and every env/header value are redacted before writing;
  key names may appear, values never.
- `observation.complete` is false with a `reason`
  (`pagination_limit`, `protocol_error`, `timeout`) whenever the tool
  surface could not be fully enumerated — partial observation is stated,
  never silently treated as the whole truth.
- No timestamps inside the structured body; wall-clock data belongs to the
  caller's log, not to a file that might be diffed.

## 6. Closing the loop — declared vs observed tools

mcprobe output finally enables the tool-level comparison sketched in the
depth order:

- config-declared tools (stage 2) vs runtime `tools` list
- undeclared runtime tools → **candidate** finding family `P104`

Comparison lives OUTSIDE mcprobe (a read-only join over mcprobe report +
secscan artifacts), keeping the observer dumb and the judge pure.

## 7. Non-goals

- No `tools/call`, no sampling, no resource/prompt subscription (later
  scopes, each with their own safety note).
- No sandboxing built-in v1 (documented guidance instead: containers,
  throwaway users). Sandboxing integration is a follow-up feature.
- No merging into baselines: fingerprints presuppose reproducibility.

## 8. Resolved decisions

Locked before implementation:

| Decision | Ruling |
|---|---|
| P104 | **v1 is report-only.** mcprobe emits observations; no blocking finding family on day one. The static↔runtime join runs read-only first, real-world behavior calibrates the corpus, and P104 becomes a deliberate spec change only if it earns it. |
| Baseline interplay | **None.** The baseline is the acknowledgement mechanism for deterministic scan findings; runtime observation is timestamped, environment-dependent and non-reproducible. mcprobe stays its own report. |
| Consent UX | `usb mcprobe ...` without `--yes` shows exactly what would run and stops. `--yes` = explicit machine consent. Piped stdin = refusal. Launch line and env/header values are redacted in every report. |
| Pagination | Default 10 pages, hard ceiling 1000 tools, repeated cursor → immediate stop, empty/malformed page → recorded protocol failure. Bound reached ⇒ `observation.complete: false` + reason; never "no tools". |

With these locked, mcprobe v1 matches USB's character end to end: narrow,
controlled, hiding nothing about what it does — and keeping observation
strictly separated from judgment.

---

## 9. HTTP/SSE transport (v1.1 design)

Carries the five stdio v1 principles to remote targets unchanged:

**Consent.** Identical surface: no `--yes` -> plan only (url, transport
class, contact=`remote-network`) and stop; piped stdin refuses. Remote is
SAFER than stdio (no process spawn) but still opt-in, because it contacts
a third-party network endpoint that a scanned config points at.

**Timeout.** Same per-phase deadline model, renamed phases:
`connect` (TCP/TLS) -> `handshake` (initialize/initialized) ->
`tools_list` -> `close`. No spawn/grace phases; the connection simply
closes. Overall runtime stays bounded by phase count x timeout.

**Redaction.** New leak surface: HEADERS and URL query strings.
- Header VALUES are never written; header NAMES may appear.
- URLs are recorded as scheme://host/path only - query strings stripped.
- env-value substitution and the generic secret pattern apply everywhere,
  exactly like stdio.

**Protocol boundary.** Scope stays tools/list ONLY; resources/prompts and
tools/call remain out of scope permanently. Wire format: JSON-RPC over
HTTP POST (`Accept: application/json` for streamable responses), with the
SSE variant parsed by reading `data:` frames from a
`text/event-stream` body - identical messages, different envelope.
Post-handshake rule inherited: an unparseable frame during an exchange is
protocol_error, not noise. Pagination shares ONE paginator implementation
with stdio (page cap, tool ceiling, repeated-cursor stop). No auth flows
in v1: 401/403 is recorded honestly as reason `auth_required`; no retries.

**Report-only.** Same schema, adjusted fields: `transport:
"remote-http" | "remote-sse"`, `contact: "remote-network"`,
handshake keys renamed per phases above. observation semantics
(complete/reason), exit codes and "never merges into baselines or
usb-contract-artifacts" carry over verbatim. P104 stays untouched -
remote probes accumulate calibration data report-only like everything
else.

Implementation note: stdio needs a background line reader because a pipe
pushes unsolicited bytes; HTTP is request/response, so the reader thread
disappears and each phase becomes a single bounded request. Less code,
same contract.

### 9.1 Resolved: `Mcp-Session-Id` handling (streamable HTTP)

- The session id received from the initialize response IS echoed verbatim
  on every subsequent request of the same probe (spec requirement) and on
  the closing DELETE.
- The value is NEVER serialized: the report records only
  `"session_id_present": true | false`. The id is a capability token -
  holding it transiently in memory is required, publishing it is not.
- v1 does NOT open the server-initiated GET channel; tools/list needs no
  downstream requests, so no second connection exists to authenticate.
