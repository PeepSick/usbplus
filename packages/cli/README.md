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

# Reproducible install from a lock file (refuses on drift, installs nothing)
usb install --locked

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
usb mcprobe                       # dry run: lists what WOULD be contacted, contacts nothing
usb mcprobe --yes .               # consent, then probe every MCP server found under .
usb mcprobe --yes ./.mcp.json     # probe the servers in one config file
usb mcprobe --yes --timeout 10 .  # per-phase deadline (default 30s)
```

`usb mcprobe` discovers MCP servers in your configs (the same discovery as
`secscan`), performs the MCP handshake, records exposed tools, and reports
results. It never calls a tool, and without `--yes` it contacts nothing. Requires `python3`. Non-deterministic by design — see `docs/mcprobe.md`.

### Integrity (verify / lock / diff)

```bash
usb verify                    # re-hash installed skills vs the install-time manifest
usb verify intent-router      # one skill
usb verify --all --json       # machine-readable (CI-friendly)

usb lock                      # pin installed versions into usb.lock.json
usb lock-show                 # inspect what a lock pins
usb install --locked          # reproducible install; refuses + installs nothing on drift

usb diff intent-router        # installed copy vs latest catalog: capability/permission changes
usb diff old.md new.md        # any two local skill files or directories
usb diff old.md new.md --patch --json
```

Every install writes a SHA-256 manifest (`usb-manifest.json`) and a per-target
ledger next to the pack; `verify` re-hashes and reports modified / missing /
unexpected files (exit `0` clean, `1` drift, `2` error). **Integrity is not
safety** — verify proves the bytes are unchanged, not that the content is
benign; that stays `secscan`'s job. `diff` reuses the secscan engine to name
added/removed capabilities, tools, frontmatter and verdict changes. All three
are local and require `python3` (stdlib only).

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
| `usb verify` / `usb lock` / `usb diff` | ✅ | None |
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

The CLI is a bash script (zero npm dependencies; needs `curl` and `bash`) that talks to the
public [USB catalog](https://usbplus.peepsicklabs.com). Installs record what they
placed in a local registry (`~/.ai-skills/.../installed.json`). No API keys, no telemetry
by default.

Python scripts (`usb-secscan.py`, `usb-mcprobe.py`, `usb-integrity.py`,
`usb-mcp.py`) handle security scanning, runtime observation and integrity
verification. They use only Python 3 stdlib except for `usb-mcp.py` which
requires the `mcp` package.

## License

MIT — part of the USB open-source project.
