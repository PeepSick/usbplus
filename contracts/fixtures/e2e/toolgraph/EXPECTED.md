# toolgraph corpus — expected evidence (Stage 3 contract)

This file is the HAND-WRITTEN golden target for the stage 3 idiom parser
(docs/tool-graph-sources.md §8). The parser is correct only when engine
output matches every row below. Anchors are 1-based lines in the named
file; categories come from the existing capability lexicon over
`tool name + description`; strength is always `strong` (code surface, not
prose).

## notes (servers/notes_server.py, relative-path launch — availability gate)

| anchor | registration           | text scanned                                            | category        |
|-------:|------------------------|---------------------------------------------------------|-----------------|
| 12     | `@mcp.tool()`          | fetch_release_notes / Fetches release notes over http…  | network-egress  |
| 18     | `@mcp.tool()`          | save_note / Saves a note to disk next to the project.   | (none)          |

Uncategorized registrations are recorded by the parser for counting but do
NOT enter observed capabilities (no category → no P101 subject). Documented
v1 limitation.

## archive (servers/archive_server.mjs)

| anchor | registration            | text scanned                                             | category             |
|-------:|-------------------------|----------------------------------------------------------|----------------------|
| 9      | `server.tool(`          | purge_archive / Deletes old archived builds recursively  | destructive-commands |
| 16     | `server.registerTool(`  | list_archives / Lists archives and sends … status api    | network-egress       |

## remote-registry (npx launch — opaque, never parsed)

No source in tree. Expected artifact delta once implemented:

```json
"authority_scope": { "opaque": ["npx -y @example/remote-mcp"], ... }
```

No fabricated tools, no findings derived from the package name.

## Pre-parser state (current goldens)

Until the idiom parsers land, committed E2E goldens lock the CURRENT
engine output for this corpus: three healthy MCP units with NO extracted
registrations and NO opaque key. The parser PR must flip output to exactly
the tables above and refresh goldens in the same commit.
