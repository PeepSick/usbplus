# Release Notes — USB v1.0.4

**Release date:** 2026-10-19

## What's new in v1.0.4

Patch release focused on the first-run experience — five defects that each stopped a buyer at a different step, all now covered by regression checks.

- **`npm install -g` failed on Windows.** The Node launcher picked the first `bash.exe` on PATH, which is the WSL stub (`C:\Windows\System32\bash.exe`) on most machines; WSL eats the script path's backslashes and every command died with `No such file or directory`. It now prefers Git for Windows' MSYS bash, skips WSL/Store stubs, and prints install instructions when only WSL exists
- **`npm install -g` failed on macOS.** `bin/usb` was committed with CRLF line endings, so the published tarball shipped a `#!/usr/bin/env node\r` shebang. Linux ≥ 5.1 hides this by stripping the trailing CR; macOS does not. `bin/usb` is LF now, pinned by a scoped `.gitattributes` rule
- **Installed skills failed their own Trust Layer scan.** The installer wrote the display title into the frontmatter `name:` field, but Claude Code and `usb secscan` (rule S004) both require slug format — so `usb secscan` right after `usb install` reported 529 MEDIUM findings over our own catalog. `name:` now carries the slug; the title stays as the H1. Verified 529/529 `matches-declaration`, exit 0
- **Fresh self-host could not serve a single install.** `docker compose up -d` never created the database schema, so `/api/health` and `/api/version` looked healthy while every `/api/install` returned 500. Compose now runs a one-shot `migrate` service, and `drizzle.config.ts` reads `DATABASE_URL`
- **`usb mcp` dumped a raw `ModuleNotFoundError`** instead of saying the `mcp` pip package is missing. The CLI now checks before starting and prints the install commands, including the PEP 668 form Debian/Ubuntu require

## What's new in v1.0.3

**Release date:** 2026-10-16

- **mcprobe cwd contract** — stdio MCP servers now spawn in the config file's directory (as `docs/mcprobe.md` documents), so a server declared with relative args resolves its own scripts; locked by a new end-to-end regression
- `usb secscan --write-baseline FILE` accepts the space form of the flag
- Bare `?core=true` returns exactly the 9 core skills the API documents
- `/api/skills` reconciles the seeded pack row with the shipped catalog version, so live deployments serve the current pack version
- README: the Quickstart now carries the v1 surface — Trust Layer one-liners and a real `secscan` demo

## What is USB?

USB (Universal Systems Bridge) is a plug-and-play skill system for AI agents. Write a skill once, install it into any of 16 agent runtimes — Claude Code, Cursor, Ollama, and more — then verify what those capabilities actually do with the Trust Layer (`secscan`, `mcprobe`).

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

Install exactly what you need. The CLI positional argument selects a **preset
name** (`web-dev`, `backend`, `infra`, `security`, `data`, `core-only`), a
**domain** (`react-state` — all 8 workflows), or an **exact skill slug**; the
interactive menu (`usb`) and the HTTP API / SDK expose the full filter
vocabulary (`slug`, `domain`, `category`, `preset`, `core`, `exclude`) and let
you combine it freely:

```bash
usb install web-dev                  # curated preset (its domains + the 9 core skills)
usb install react-state              # one domain — all 8 of its workflows
usb install intent-router            # a single skill by exact slug
usb install --dry-run web-dev        # preview the script without executing it
usb install --target=claude web-dev  # force a specific runtime

# Full filter vocabulary via the API / SDK — combine freely:
#   ?preset=web-dev&core=true     ?domain=react-state
#   ?category=Audit,Security      ?exclude=python-*,node-*
```

## Upgrade from v0.4.x

```bash
npm install -g @peepsick/usb-cli@latest
usb install --target=<your-runtime>
```

## Packages

| Package | Version | Where |
|---------|---------|-------|
| `@peepsick/usb-cli` | 1.0.4 | [npm](https://www.npmjs.com/package/@peepsick/usb-cli) |
| `@peepsick/usb-sdk` | 1.0.3 | [npm](https://www.npmjs.com/package/@peepsick/usb-sdk) |
| Community agents (`packages/community/*`) | source | this repo — publish to npm yourself |

The community packages (`@peepsick/usb-community-*`, e.g. the OAuth agent under
`packages/community/oauth-agent`) are **source-only**: they ship in this
repository and are published to npm per-package by their own maintainers, not as
part of the CLI release train.

## Links

- Website: [usbplus.peepsicklabs.com](https://usbplus.peepsicklabs.com)
- GitHub: [github.com/PeepSick/usbplus](https://github.com/PeepSick/usbplus)
- npm: [@peepsick/usb-cli](https://www.npmjs.com/package/@peepsick/usb-cli)
