# Changelog

All notable changes to USB — Universal Skill Bridge will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.1] — 2026-08-31

### Fixed
- SDK `@peepsick/usb-sdk` version aligned with CLI `@peepsick/usb-cli` (both 1.0.1)
- README and landing page inconsistencies
- `usb list` command broken output

## [1.0.0] — 2026-08-28

### Added
- **Trust Layer** — USB discovers AI capabilities inside repositories and verifies whether their observed behavior matches their declared contract
- **secscan** — Skill Contract Verifier: deterministic verdicts per skill (matches-declaration, purpose-mismatch, excessive-authority, unverifiable)
- **mcprobe** — MCP runtime observation: stdio + HTTP/SSE transport probing, consent-enforced, report-only v1
- **MCP server** — `usb_scan` + `usb_mcprobe` tools over stdio transport, 26/26 conformance tests
- **Scan baselines** — `--baseline` flag so CI gates only on new findings
- **Contract artifacts** — `--emit-contracts` with conformance suite proving re-derivation
- **MCP adapter** — config-layer contract verification for `.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`
- **User-scope MCP configs** — `--system` flag reads `~/.claude.json`, `~/.cursor/mcp.json`, etc.
- **Declared tool lists** — depth stage 2 reads entry `tools` + Cursor `mcpAllowlist`
- **Claude Code permissions** — settings-layer rules as authority scope
- **Excessive authority analysis** — P103 advisory for MCP authority scope
- **Stage 3 idiom parser** — tool registrations as CapabilityEvidence from Python/TypeScript source
- **Agent/command adapter** — declared tool surface from frontmatter
- **E2E reference corpus** — acme-agent 18 units with normalized goldens
- **529 skills** — 9 hand-written core skills + 65 domains × 8 workflows
- **16 provider targets** — claude, cursor, leosis, hermes, openai, anthropic, langchain, mcp, openrouter, groq, mistral, ollama, lm-studio, vllm, auto, generic
- **Filter system** — slug, domain, category, preset, core, exclude query params
- **6 presets** — core-only, web-dev, backend, infra, security, data
- **Community packages** — `@peepsick/usb-community-oauth-agent`
- **Playground** — `/playground/[slug]` for interactive skill preview
- **Installations API** — opt-in installation logging (`USB_LOG_INSTALLATIONS=on`)
- **CI workflow** — readme-check.yml with 9 verification scripts + conformance runners

### Changed
- Legacy SSE transport explicitly unsupported (spec deprecated, real servers return 405)
- `auth_required` vs `bot_blocked` distinction locked (403 with WWW-Authenticate = auth_required)

### Fixed
- `DISCOVERY_SKIP_DIRS` unified — dependency/build dirs never walked
- mcprobe UA hotfix for remote servers
- mcprobe remote auth detection calibrated

## [0.4.7] — 2026-08-20

### Fixed
- README/landing page inconsistencies
- `usb list` broken output

## [0.4.6] — 2026-08-18

### Fixed
- Interactive picker hanging on Windows (CRLF stripped from select/read input)
- CLI failing on Windows when bash isn't on PATH
- Installer prompt spinning forever instead of waiting for input

### Changed
- README restructured: landing page flow, not documentation

## [0.4.2] — 2026-08-15

### Added
- Initial public release
- Skill catalog with domain × workflow matrix
- CLI installer with runtime auto-detection
- SHA256 verification for install scripts
