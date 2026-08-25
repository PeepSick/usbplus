# @peepsick/usb-cli

> The Universal Skill Bridge command line.

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
curl -fsSL https://usb.peepsicklabs.com/usb -o /usr/local/bin/usb && chmod +x /usr/local/bin/usb
```

## Usage

```bash
# Interactive picker — pick what to install
usb

# Install the whole pack (auto-detects runtime)
usb install

# Install one skill by slug
usb install intent-router

# Install one domain (8 workflows)
usb install react-state

# Install a curated preset
usb install web-dev
usb install backend
usb install security

# Just the 9 core orchestration skills
usb install core-only

# Search the catalog
usb search oauth

# Show details about one skill
usb info intent-router

# Show pack version
usb version

# Security-scan skills before trusting them (Skill Contract Verifier)
usb secscan                        # project-local skills (.claude/skills, skills, .cursor/rules)
usb secscan --system               # installed skill locations on this machine
usb secscan --repo owner/repo      # fetch a git/GitHub repo read-only and scan it
usb secscan ./some/skills --json   # machine-readable report
usb secscan --write-baseline .     # snapshot findings; then gate CI:
usb secscan --baseline .usb-secscan-baseline.json   # only NEW findings block
```

## Security scanning

`usb secscan` verifies each skill's contract: does the body only do what the
frontmatter declares? Undeclared capabilities (credential access, pipe-to-shell
network egress, destructive deletion, persistence hooks) are findings; vague or
silent-agency phrasing is reported as advisory signals and never blocks on its
own. Deterministic output, `--json` for CI, exit 1 when a contract is violated.
Requires `python3`. See the repository README ("Security scanning skills") for
the rule catalog.

## How it works

The CLI is a thin bash wrapper (~6 KB, zero dependencies beyond `curl` and
`bash`) that talks to the public [USB catalog](https://usb.peepsicklabs.com).
No local state, no API keys, no telemetry by default.

## License

MIT — part of the USB open-source project.
