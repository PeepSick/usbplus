# mcprobe calibration log

Report-only runtime observations against real remote MCP servers.
Purpose: accumulate static↔runtime comparison data BEFORE deciding
whether a P104 finding family is justified. Raw `usb-mcprobe-report/v1`
outputs are committed next to these notes.

## Run 1 — 2026-08-25 (mcprobe v1.1 + UA hotfix)

Config: 4 remote entries (see raw JSON). Engine exit 1 — expected: one
target intentionally requires auth.

| Target | Transport | Result | Tools | Docs↔runtime |
|---|---|---|---|---|
| deepwiki-http (`mcp.deepwiki.com/mcp`) | streamable HTTP | complete ✓ | ask_question, read_wiki_contents, read_wiki_structure | **exact match** with published tool list |
| deepwiki-sse (`/sse`) | legacy SSE | protocol_error: HTTP 405 | — | known v1 limitation (legacy GET-stream transport unsupported; modern spec deprecates it) |
| web-reader (`mcp.vanshul.com/mcp`) | streamable HTTP | complete ✓ **after UA fix** | extract_links, fetch_markdown, fetch_metadata, search_page | **exact match** with published tool list |
| github-copilot-auth | streamable HTTP | auth_required (HTTP 401) ✓ | — | honest error-path calibration |

### Findings from real-world data

1. **Declared↔runtime agreement: 2/2 exact matches** on comparable cases.
   No divergence observed → P104 stays unwritten; report-only remains the
   right call until a real mismatch shows up.
2. **Static-layer blindness confirmed by design**: remote servers have no
   in-tree source, so the stage-3 parser sees nothing — mcprobe is the
   only way USB can see them at all. Validates the runtime layer's reason
   to exist.
3. **Missing default User-Agent caused Cloudflare 403**, which our
   vocabulary classified as `auth_required`. Fixed in-engine (UA header
   added). Open nuance for later: distinguishing bot-protection blocks
   from genuine auth failures may deserve separate reasons.
4. **Legacy SSE transport (GET-stream + POST endpoint) unsupported**;
   modern streamable HTTP works everywhere tested. Limitation documented,
   support deferred.

### P104 verdict so far

NOT JUSTIFIED YET. Zero declared↔runtime mismatches across the first
real sample. Keep collecting report-only data as new remote targets are
probed.
