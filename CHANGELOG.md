# Changelog

All notable changes to USB — Universal Systems Bridge will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.4] — 2026-10-19

### Fixed
- **Fresh self-host could not serve a single install.** `docker compose up -d` never created the database schema: nothing ran a migration, so `/api/health` and `/api/version` (which don't touch Postgres) returned 200 while every `/api/install` failed with `relation "skill_packs" does not exist` and the 500 stub *Installer could not be generated* — the site looked healthy and was unusable. Compose now runs a one-shot `migrate` service (waits for Postgres, runs `drizzle-kit push`) that `app` depends on, `drizzle.config.json` became `drizzle.config.ts` so it reads `DATABASE_URL`, and `npm run db:push` was added. Verified on a fresh clone of the pushed commit: `/api/install` returns a 6.01 MB installer, `install-sha256` matches byte-for-byte, and the catalog seeds 529 skills
- **Windows launcher picked the WSL bash stub and every command failed.** `bin/usb` searched PATH for `bash.exe` first; on most Windows boxes the first hit is `C:\Windows\System32\bash.exe` (WSL) or the Microsoft Store stub. WSL forwards the script path to the Linux side, which eats the backslashes — `C:\Users\...\usb.sh` arrived as `C:Users...\usb.sh` and bash died with `No such file or directory` before any command ran. The launcher now prefers **Git for Windows' MSYS bash** (the only bash that understands `C:\...` script paths natively), falls back to any non-stub `bash.exe` on PATH (Cygwin, MSYS2), and — when only WSL is present — exits with install instructions instead of a cryptic path error
- **Installed skills failed their own Trust Layer scan.** The installer wrote the human-readable title (`A/B Testing Framework: Audit`) into the frontmatter `name:` field. Both Claude Code and `usb secscan` (rule **S004**) require `name` to match `^[a-z0-9][a-z0-9_-]{0,63}$`, so `usb secscan ~/.claude/skills` right after `usb install` reported **529 MEDIUM structural findings** and exit 1 over the project's own catalog. The installer now writes the **slug** into `name:`; the display title stays as the H1. Verified:529/529 skills now come back `matches-declaration`, exit 0
- **`npm install -g` broke on macOS.** `bin/usb` was committed with CRLF line endings, so the published tarball shipped a `#!/usr/bin/env node\r` shebang. npm executes the bin entry directly on macOS/Linux, where `/usr/bin/env` receives `node^M` and fails with `No such file or directory`. Linux ≥ 5.1 hides the bug by stripping the trailing CR (which is why it worked on Linux), macOS does not. `bin/usb` is now LF, and a scoped `.gitattributes` rule keeps it that way

## [1.0.3] — 2026-10-16

### Added
- `scripts/check-mcprobe.py` case **D2 — cwd contract**: a stdio server declared with relative args must probe end-to-end with its cwd equal to the config directory; the stub asserts its cwd against a config-declared env var, so the contract is locked exactly

### Fixed
- `mcprobe` stdio servers now spawn with **cwd = the config file's directory** (as `docs/mcprobe.md` documents: `spawn(command, args, env) # cwd: config dir`). Before, the child searched *mcprobe's* cwd for its own relative args and died on the handshake
- `usb secscan --write-baseline FILE` now consumes the file in the space form exactly like the engine's optional-value argument; previously the file name was silently treated as a scan root and the baseline landed elsewhere
- `/api/skills` reconciles the seeded `skill_packs` row with the shipped catalog version: a deployment seeded at an older release now serves the current pack version on first use (before, the row kept its seed-time version forever — version checks and install scripts reported a stale pack version)

### Changed
- README: the Quickstart carries the v1 surface — `usb secscan` / `usb mcprobe` one-liners plus a real streaming `secscan` demo (replacing the v0.x runtime-picker gif); contact addresses moved to `info@peepsicklabs.com`

## [1.0.2] — 2026-10-15

### Added
- `usb install <domain>` now works as documented: a bare domain sent as `slug=` falls back to a domain-wide install (all 8 workflows) across `/api/install`, `/api/install-sha256`, `/api/skills` and the MCP catalog — hash verification included
- `usb secscan --emit-contracts <file>` forwarded from the CLI wrapper (contract artifacts without invoking the engine directly)
- README: dedicated **mcprobe** documentation — consent model, transports, redaction, report contract and exit codes

### Changed
- README restructured for the release: contents navigation, secscan's deep-dive collapsed into an expandable section, preset counts corrected (73 skills each), portable-target count corrected (11 + `auto`), `usb version` behavior documented
- Preset labels state the exact count (`Web Dev Pack (73 skills)`) instead of rough estimates
- `curl -fsSL https://usbplus.peepsicklabs.com/usb` now serves the same CLI as the npm package — Trust Layer commands included; a missing engine file points to `npm install -g @peepsick/usb-cli` instead of failing opaquely
- Engine-missing errors for `mcprobe`/`mcp` name the missing file and the install path

### Fixed
- Bare `?core=true` now returns only the 9 core skills, as the API always documented (it previously returned the full pack; combined with another selection filter it still adds the core skills to that selection)
- `.github/workflows/ci.yml`: removed an invalid second workflow document appended to the file (Actions rejected the whole workflow); added `scripts/check-mcp.py` (stdio MCP conformance) to CI
- `CONTRIBUTING.md` duplicate section removed
- Removed stray artifacts that never belonged to the published tree (unrelated-project `docs/` stubs, placeholder `tests/` that broke `typecheck`, a corrupted README duplicate, a stale `usbv2.zip`)

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
