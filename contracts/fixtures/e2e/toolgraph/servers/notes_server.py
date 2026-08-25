"""Minimal FastMCP server used by the toolgraph conformance corpus.

Hand-counted registrations (see EXPECTED.md):
    line 12 @mcp.tool() -> fetch_release_notes  (network-egress via lexicon)
    line 18 @mcp.tool() -> save_note            (no category)
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("notes")


@mcp.tool()
def fetch_release_notes(repo: str) -> str:
    """Fetches release notes over http from the project endpoint."""
    raise NotImplementedError


@mcp.tool()
def save_note(path: str, content: str) -> str:
    """Saves a note to disk next to the project."""
    raise NotImplementedError
