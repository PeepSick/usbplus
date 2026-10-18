# @peepsick/usb-cli

> The Universal Systems Bridge command line.

Install 529 original agent skills (65 hand-researched domains × 8 workflows,
plus 9 core skills) into Claude Code, Hermes, LeoSIS, Cursor, OpenAI, Anthropic,
LangChain, MCP, OpenRouter, Groq, Mistral, Ollama, LM Studio, vLLM, or any
custom path — with one command.

## Install

```bash
npm install -g @peepsick/usb-cli
```

Or skip the package and grab the standalone bash script:

```bash
curl -fsSL https://usbplus.peepsicklabs.com/usb -o /usr/local/bin/usb && chmod +x /usr/local/bin/usb
```

## Usage

### Skill Management

```bash
# Interactive picker — browse domains, categories, presets or skills
usb

# Install the whole pack (auto-detects your runtime)
usb install

# Install one skill by exact slug
usb install intent-router

# Install a curated preset — the positional argument is a preset name
# (web-dev, backend, infra, security, data, core-only) or an exact skill slug
usb install web-dev
usb install security
usb install core-only          # just the 9 core orchestration skills

# Force a runtime, or preview the script without executing it
usb install --target=claude web-dev
usb install --dry-run web-dev

# Search the catalog
usb search oauth

# Show details about one skill
usb info intent-router

# Show pack version
usb version
```

### Security Scanning (Skill Contract Verifier)

```bash
usb secscan                        # project-local skills (.claude/skills, skills, .cursor/rules)
usb secscan --system               # installed skill locations on this machine
usb secscan --repo owner/repo      # fetch a git/GitHub repo read-only and scan it
usb secscan ./some/skills --json   # machine-readable report
usb secscan --write-baseline .     # snapshot findings; then gate CI:
usb secscan --baseline .usb-secscan-baseline.json   # only NEW findings block
```

### Runtime Observation (MCP Probe)

```bash
usb mcprobe stdio:/path/to/server         # probe a local MCP server via stdio
usb mcprobe https://example.com/mcp       # probe a remote MCP server via HTTP
usb mcprobe --yes stdio:npx -y @scope/pkg # skip interactive consent prompt
```

`usb mcprobe` performs the MCP handshake, records exposed tools, and reports
results. Requires `python3`. Non-deterministic by design — see `docs/mcprobe.md`.

### MCP Server

```bash
usb mcp    # start the MCP stdio server (for AI agents)
```

Exposes `usb_scan` and `usb_mcprobe` as MCP tools. Requires `python3` and
the `mcp` Python package.

## Python Dependencies

| Command | Python 3 only | Extra pip package |
|---------|---------------|-------------------|
| `usb secscan` | ✅ | None |
| `usb mcprobe` | ✅ | None |
| `usb mcp` | ✅ | `mcp` |

For the MCP server:

```bash
python3 -m pip install mcp
# or, from the installed package
python3 -m pip install -r "$(npm root -g)/@peepsick/usb-cli/requirements.txt"
```

On Debian/Ubuntu, Python is an *externally-managed environment* (PEP 668) and
the commands above are refused with `error: externally-managed-environment`.
Use one of:

```bash
python3 -m pip install --break-system-packages mcp   # system-wide
python3 -m venv ~/.usb && ~/.usb/bin/pip install mcp # isolated
```

`usb mcp` checks for the package before starting and prints these commands if
it is missing, instead of surfacing a `ModuleNotFoundError` traceback to the
MCP client.

## How it works

The CLI is a thin bash wrapper (~6 KB, zero dependencies beyond `curl` and
`bash`) that talks to the public [USB catalog](https://usbplus.peepsicklabs.com).
No local state, no API keys, no telemetry by default.

Python scripts (`usb-secscan.py`, `usb-mcprobe.py`, `usb-mcp.py`) handle
security scanning and runtime observation. They use only Python 3 stdlib
except for `usb-mcp.py` which requires the `mcp` package.

## License

MIT — part of the USB open-source project.
