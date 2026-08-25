// Minimal MCP SDK server used by the toolgraph conformance corpus.
// Hand-counted registrations (see EXPECTED.md):
//   line 9  server.tool(...)     -> purge_archive  (destructive-commands)
//   line 16 server.registerTool  -> list_archives  (network-egress)
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

const server = new McpServer({ name: "archive" });

server.tool(
  "purge_archive",
  "Deletes old archived builds recursively",
  {},
  async () => ({ content: [] })
);

server.registerTool(
  "list_archives",
  {
    description: "Lists archives and sends summaries to the status api",
  },
  async () => ({ content: [] })
);
