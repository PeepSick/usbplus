# Release Notes — USB v1.0.1

**Release date:** 2026-08-31

## What is USB?

USB (Universal Skill Bridge) is a plug-and-play skill system for AI agents. Write a skill once, install it into any of 16 agent runtimes — Claude Code, Cursor, Ollama, and more.

## Highlights

### Trust Layer (new in v1.0.0)

USB discovers AI capabilities inside repositories and verifies whether their observed behavior matches their declared contract. This includes:

- **Skill contracts** — S/P/A verification with file:line evidence and deterministic verdicts
- **MCP contracts** — config-layer evidence for MCP servers (transport, command line, environment)
- **Scan baselines** — CI gates only on new findings when scanning third-party collections
- **Runtime observation** — mcprobe for stdio + HTTP/SSE transport probing

### 529 Skills

9 hand-written core skills + 65 engineering domains × 8 workflows (Audit, Plan, Build, Script, Diagnose, Harden, Explain, Tune).

### 16 Provider Targets

| Tier | Targets |
|------|---------|
| Native install | claude, cursor, leosis, hermes |
| Portable bundle | openai, anthropic, langchain, mcp, openrouter, groq, mistral, ollama, lm-studio, vllm, generic |
| Auto-detect | auto |

### Filter System

Install exactly what you need:

```bash
usb install --preset=web-dev          # curated bundle
usb install --domain=react-state      # one domain
usb install --category=Security       # by category
usb install --slug=intent-router      # single skill
usb install --exclude=python-*        # exclude patterns
```

## Upgrade from v0.4.x

```bash
npm install -g @peepsick/usb-cli@latest
usb install --target=<your-runtime>
```

## Packages

| Package | Version | Description |
|---------|---------|-------------|
| `@peepsick/usb-cli` | 1.0.1 | Terminal CLI |
| `@peepsick/usb-sdk` | 1.0.1 | TypeScript SDK |
| `@peepsick/usb-community-oauth-agent` | 0.1.0 | OAuth hardening agent |

## Links

- Website: [usb.peepsicklabs.com](https://usb.peepsicklabs.com)
- GitHub: [github.com/PeepSick/usb](https://github.com/PeepSick/usb)
- npm: [@peepsick/usb-cli](https://www.npmjs.com/package/@peepsick/usb-cli)
