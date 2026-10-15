# Release e2e harness

Two matrices that exercise a **running USB build end-to-end** — the battery
that gates a release before it ships. They complement the offline python
suites (`scripts/check-*.py`, `contracts/conformance/`), which verify
engines and contracts in isolation; these verify the assembled product.

## What it covers

- **`http_matrix.py`** — HTTP API matrix: health/version, catalog + filter
  semantics (presets, domains, slugs, categories, exclude, `core=true`,
  the documented slug→domain fallback), install-script vs
  `/api/install-sha256` **byte-parity for every filter combo** (the
  installer verifies that hash before running — parity is the contract),
  landing pages, and the HTTP MCP surface (`tools/list`, `usb_search`,
  `usb_render_install`, unknown-tool errors).
- **`cli_matrix.sh`** — CLI + Trust Layer matrix over the real `usb.sh`:
  version/list/search/info, dry-runs (slug, preset, domain, bare prefix),
  the interactive picker over CRLF input, a **real install** into a fake
  `$HOME`, `secscan` over the installed tree (verdicts, `--json`, baseline
  acknowledge), `mcprobe` consent refusal **plus live observation** of a
  stub stdio MCP server (handshake + tools, no tool call), and the stdio
  MCP server (`usb mcp`) tool listing.

## How to run

Everything runs from the repo root against a locally served build.

```bash
# 1. Postgres (isolated container) + schema
docker run -d --name usb-test-pg \
  -e POSTGRES_USER=usb -e POSTGRES_PASSWORD=usb_test_pw -e POSTGRES_DB=usb \
  -p 127.0.0.1:55432:5432 postgres:16-alpine
npx drizzle-kit push --config tests/release/drizzle.test.json

# 2. Build + serve
DATABASE_URL=postgres://usb:usb_test_pw@127.0.0.1:55432/usb npm run build
DATABASE_URL=postgres://usb:usb_test_pw@127.0.0.1:55432/usb npm run start

# 3. The matrices
python3 tests/release/http_matrix.py http://127.0.0.1:3000
USB_BASE_URL=http://127.0.0.1:3000 bash tests/release/cli_matrix.sh
```

Both print one PASS/FAIL line per check and exit non-zero on any failure.

## Platform notes

- `cli_matrix.sh` needs bash with `curl` and `python3` (Git Bash on
  Windows). Where `python3` is the WindowsApps alias (Python Install
  Manager), the harness falls back to a real interpreter for the stub MCP
  server, because the alias misbehaves under mcprobe's minimized env —
  that behavior is a launcher quirk, not a probe bug.
- The fake `$HOME` install is target `claude` by design; the scan that
  follows must report honest verdicts (exit 0 = clean, 1 = findings), and
  `--write-baseline` + `--baseline` must acknowledge exactly what was
  found before.
