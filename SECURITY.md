# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in USB, please report it responsibly.

**Do not open a public issue.** Instead, email [info@peepsickai.com](mailto:info@peepsickai.com) with:

- Description of the vulnerability
- Steps to reproduce
- Potential impact assessment
- Any suggested fix (if applicable)

We will respond within 48 hours and work with you to understand and address the issue before any public disclosure.

## Security Measures

### Install Script Verification

USB install scripts are SHA256-verified before execution:

```bash
curl -fsSL https://usb.peepsicklabs.com/api/install?target=auto -o install.sh
EXPECTED=$(curl -fsSL https://usb.peepsicklabs.com/api/install-sha256?target=auto)
echo "$EXPECTED  install.sh" | sha256sum -c -
```

### Skill Contract Verification

Before installing skills you did not write, use `usb secscan` to verify:

```bash
usb secscan                       # project-local skills
usb secscan --system              # installed skills + user-scope MCP configs
usb secscan --repo owner/repo     # fetch and scan a remote repo
```

### Content Isolation

- Skill content is embedded into bash heredocs with proper escaping
- No dynamic code execution from untrusted sources
- All bundled scripts are behavior-evidence for security scanning

### Runtime Safety

- mcprobe (MCP runtime observation) is **report-only** — it never modifies target servers
- Consent is required for all probing operations
- Remote transports are excluded from certain analyses (their egress is already observed behavior)

## Scope

The following are in scope:

- `@peepsick/usb-cli`
- `@peepsick/usb-sdk`
- `usb.peepsicklabs.com` website and API
- Install scripts served by `/api/install`
- MCP server endpoint `/api/mcp`

## Out of Scope

- Third-party skills not published by PeepSick Labs
- Agent runtimes that consume USB skills (their security is their own responsibility)
- Social engineering attacks

## Disclosure Policy

We follow coordinated disclosure:

1. Report received → acknowledged within 48 hours
2. Investigation → fix developed within 7 days for critical issues
3. Fix released → public disclosure after fix is available
4. Credit given to reporter (unless anonymity requested)
