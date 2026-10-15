#!/usr/bin/env python3
"""usb secscan engine - Skill Contract Verifier for agent skill files.

Scans skill definitions (Claude-style ``SKILL.md`` directories, USB pack
markdown cards, cursor ``.mdc`` rules) and answers one question per skill:

    Does this skill do what it declares, quietly do something it never
    declared, or demand authority that its job does not justify?

It never executes what it scans, never opens a network connection, and never
writes outside stdout.

The model
---------
Declared Contract   name/description/trigger fields in the frontmatter: what
                    the skill PROMISES to do.
Observed Behavior   capability evidence found in the file body: touching
                    credential material, piping network responses into a
                    shell, destructive filesystem operations, process
                    execution, persistence hooks.
Scope Check         observed capabilities minus declared capabilities. Every
                    capability category that is observed but never declared
                    becomes a P101 finding - THE core contract violation.
Authority Signals   linguistic patterns that grant vague or silent agency
                    ("without asking", "use your judgment", instruction
                    overrides, encoded blobs, invisible characters). These
                    are SIGNALS, not findings: a language pattern alone is
                    never a verdict and never blocks. They become blocking-
                    adjacent evidence only when the SAME skill also has an
                    undeclared capability (P101), in which case they are
                    attached to the report as corroborated findings.
Structural Checks   is this a well-formed skill at all (S-rules)?

MCP config layer    server entries in well-known project configs (.mcp.json,
                    .cursor/mcp.json, .vscode/mcp.json) are scanned with the
                    same contract model: the entry NAME is the declaration,
                    its command/args/url/env are observed behavior and
                    requested authority. A config that cannot be parsed is
                    reported (S101) instead of silently skipped.

Verdicts (deterministic, one per skill)
---------------------------------------
    unverifiable          frontmatter cannot be parsed, so no contract
                          can even be read (S002)
    purpose-mismatch      at least one observed capability was never
                          declared (P101)
    structural-issues     parseable but structurally broken/incomplete
                          (S-rules only)
    matches-declaration   everything observed is covered by the declaration;
                          authority signals may still be noted, but the
                          contract holds

Determinism contract
--------------------
Identical input tree -> byte-identical output: no timestamps, codepoint-only
sorting, fully ordered walks, deterministic caps (first N by line number),
UTF-8-pinned stdout, colors only on a TTY, JSON dumped with sorted keys.
Exit codes let CI gate on contract violations, not vibes:

    exit 0   no findings at medium severity or above (signals never count)
    exit 1   at least one medium/high finding
    exit 2   operational error (bad arguments, unreadable path)

Usage
-----
    usb-secscan.py [--json] [--system] [--path KIND:PATH]... [PATH]...

    PATH...            local paths to scan (kind defaults to "local")
    --path KIND:PATH   typed source; KIND is local|system|repo (first colon
                       splits; the PATH half may itself contain colons)
    --system           scan installed-system skill locations (~/.claude/
                       skills, ~/.leosis/skills, ~/.hermes/skills,
                       $AI_SKILL_HOME or ~/.ai-skills, honoring the matching
                       *_DIR env overrides used by the installer) plus
                       user-scope MCP server configs (~/.claude.json,
                       ~/.cursor/mcp.json, Windsurf, Claude Desktop)
    --json             machine-readable report on stdout

With no sources at all, project-local defaults are scanned (.claude/skills,
skills, .cursor/rules).

Rule catalog: repository README, section "Security scanning skills".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

VERSION = "1.5.0"
TOOL = "usb-secscan"

# --- documented limits (part of the deterministic contract) ------------------
MAX_DEPTH = 10                      # directory levels below a root
MAX_FILES_PER_ROOT = 5000           # regular files considered per root
MAX_SKILLS = 800                    # units analyzed overall
MAX_FILE_BYTES = 512 * 1024         # larger files are noted, not scanned
MAX_FINDINGS_PER_RULE = 5           # per rule per skill, kept by line order
DESCRIPTION_MAX_CHARS = 1024        # Claude Code hard limit on descriptions
BODY_EMPTY_CHARS = 20               # shorter body than this counts as empty
QUOTE_MAX_CHARS = 200               # evidence quotes are trimmed to this
NOTE_LIST_CAP = 20                  # per-root notes lists are capped here
CORROBORATION_DETAIL_CAP = 3        # signal lines cited inside a P101 detail

# Baseline mode: a recorded snapshot of known findings. Findings whose
# fingerprint exists in the baseline are "acknowledged" (never block);
# only NEW findings gate the exit code. Fingerprints hash rule+quote, so
# they survive line drift and temp-clone path changes.
BASELINE_DEFAULT_PATH = ".usb-secscan-baseline.json"

# Bundled executable files: modern skill bundles ship runnable helpers next
# to their SKILL.md. What those files DO is behavior evidence for the owning
# skill's contract, exactly like its prose - only harder to argue with.
SCRIPT_EXTENSIONS = {".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".py", ".ps1"}
# ONE discovery contract for BOTH walks (project discovery AND bundled-
# script inspection): dependency/build output directories are never
# descended into. Vendored first-party code ("vendor/") is deliberately
# NOT skipped - vendored capability artifacts are legitimate targets.
DISCOVERY_SKIP_DIRS = frozenset(
    {"node_modules", ".git", ".next", "dist", "build"})
MAX_SCRIPTS_PER_SKILL = 20          # executable files inspected per bundle
MAX_SCRIPT_BYTES = 256 * 1024       # larger scripts are skipped entirely
MAX_SCRIPT_DEPTH = 3                # directory levels below the skill dir

# Excessive-authority analysis (P103): an authority item is "explained" when
# its own vocabulary appears in the declared purpose text. Generic tokens
# carry no meaning on either side (every credential key contains KEY or
# TOKEN; every path contains usr or home) and are ignored on both sides.
CREDENTIAL_DESCRIPTOR_RE = re.compile(
    r"(token|secret|password|passwd|private[_-]?key|api[_-]?key|credential)",
    re.IGNORECASE)
# A filesystem-flavored purpose ("filesystem", "file organizer", "backup
# sync") explains PATH mounts categorically - the specific directory names
# need not echo the claim.
FILESYSTEM_PURPOSE_RE = re.compile(
    r"file|folder|director|storage|backup|sync|mount|fs", re.IGNORECASE)
MCP_GENERIC_ITEM_TOKENS = frozenset((
    "token", "secret", "password", "passwd", "key", "api", "auth",
    "credential", "credentials", "var", "env", "prod", "production",
    "staging", "dev", "test", "the"))
MCP_GENERIC_PATH_SEGMENTS = frozenset((
    "home", "users", "user", "opt", "usr", "local", "bin", "sbin", "tmp",
    "etc", "var", "lib", "app", "data", "srv", "mnt", "media", "windows",
    "program", "files", "x86", "js", "mjs", "cjs", "ts", "py", "sh",
    "json", "yaml", "yml", "md", "exe", "dll", "com"))

# MCP config layer (contract-core depth-order stage 1): agent runtimes read
# server entries from well-known JSON files. Discovery is filename-based and
# deterministic - no content sniffing.
#
# Project scope: the three relpaths below are probed under every scanned
# root directory. User scope (--system): mcp_system_sources() lists the
# per-runtime config FILES in the user's home; ~/.claude.json is tolerated
# as a state dump - its top-level mcpServers key may be absent, which means
# zero servers rather than a finding.
MCP_CONFIG_RELPATHS = (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json")
MCP_CONFIG_BASENAMES = {
    ".mcp.json", "mcp.json", ".claude.json", "mcp_config.json",
    "claude_desktop_config.json",
}
MCP_MAX_SERVERS_PER_CONFIG = 200    # units extracted per config file
MCP_MAX_TOOLS_DECLARED = 200        # declared tools recorded per server
# args elements that look like mounted filesystem paths (authority scope)
MCP_MOUNT_RE = re.compile(r"^(?:~|[A-Za-z]:[\\/]|[\\/])")

# --- stage 3: implementation-source idiom extraction (docs/tool-graph-
# sources.md). Exact idioms only, no AST, no third-party frameworks:
#   Python:     @<var>.tool()            followed by `def <name>`
#   TypeScript: server.tool("name", ...) / server.registerTool("name", {...})
# A registration yields CapabilityEvidence categorized by the existing
# lexicon over `name + description`, anchored at the definition line,
# strength always strong (code surface, not prose).
MAX_SOURCE_FILES_PER_UNIT = 5       # launch-line files parsed per entry
MAX_REGISTRATIONS_PER_FILE = 50     # registrations extracted per file
MAX_TS_CALL_SPAN = 12               # lines scanned inside a TS call

PY_TOOL_DECORATOR_RE = re.compile(r"^\s*@(\w+)\.tool\b")
PY_DEF_RE = re.compile(r"^\s*def\s+(\w+)")
TS_TOOL_CALL_RE = re.compile(r"^\s*server\.(tool|registerTool)\s*\(")
TS_STRING_LIT_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')
TS_DESCRIPTION_KEY_RE = re.compile(
    r'"?description"?\s*:\s*"([^"]*)"', re.IGNORECASE)
CODE_SOURCE_EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".ts"}


def _py_docstring(lines, start_idx):
    """First sentence of the def's docstring, deterministic: the text
    between the opening and closing triple quotes (same line or spanning
    at most 3 further lines). Empty when absent."""
    opened = None
    buf = []
    for ln in lines[start_idx:start_idx + 6]:
        if opened is None:
            if '"""' not in ln and "'''" not in ln:
                continue
            marker = '"""' if '"""' in ln else "'''"
            opened = marker
            part = ln.split(marker, 1)[1]
        else:
            part = ln
        if opened in part:
            buf.append(part.split(opened)[0])
            break
        buf.append(part)
    text = " ".join(p.strip() for p in buf if p.strip())
    return trim_quote(text.rstrip('"\'')) if text else ""


def extract_python_registrations(lines):
    """-> [{name, description, line}] for @<var>.tool decorators."""
    found = []
    i = 0
    while i < len(lines) and len(found) < MAX_REGISTRATIONS_PER_FILE:
        m = PY_TOOL_DECORATOR_RE.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        dm = PY_DEF_RE.match(lines[j]) if j < len(lines) else None
        if dm:
            found.append({"name": dm.group(1),
                          "description": _py_docstring(lines, j + 1),
                          "line": i + 1})
            i = j
        i += 1
    return found


def _ts_call_chunk(lines, start_idx):
    """Lines of the call starting at start_idx, until parentheses balance
    (bounded by MAX_TS_CALL_SPAN)."""
    depth = 0
    seen_open = False
    chunk = []
    for ln in lines[start_idx:start_idx + MAX_TS_CALL_SPAN]:
        chunk.append(ln)
        depth += ln.count("(") - ln.count(")")
        if "(" in ln:
            seen_open = True
        if seen_open and depth <= 0:
            break
    return "\n".join(chunk)


def _strings_in_order(chunk):
    out = []
    for a, b in TS_STRING_LIT_RE.findall(chunk):
        out.append(a if a != "" or b == "" else b)
    return [s for s in out]


def extract_ts_registrations(lines):
    """-> [{name, description, line}] for server.tool(...) and
    server.registerTool(...) calls. tool(): name = first string literal,
    description = second. registerTool(): description read from its
    'description' key."""
    found = []
    i = 0
    while i < len(lines) and len(found) < MAX_REGISTRATIONS_PER_FILE:
        m = TS_TOOL_CALL_RE.match(lines[i])
        if not m:
            i += 1
            continue
        chunk = _ts_call_chunk(lines, i)
        strings = _strings_in_order(chunk)
        name = strings[0] if strings else ""
        desc = ""
        if m.group(1) == "tool":
            if len(strings) > 1:
                desc = strings[1]
        else:
            km = TS_DESCRIPTION_KEY_RE.search(chunk)
            if km:
                desc = km.group(1)
        if name.strip():
            found.append({"name": name.strip(),
                          "description": trim_quote(desc),
                          "line": i + 1})
        i += 1
    return found


def extract_registrations(path: Path):
    """Dispatch on extension; unreadable/binary/unknown -> []."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:8192]:
        return []
    lines = raw.decode("utf-8", errors="replace").lstrip("\ufeff") \
        .splitlines()
    ext = path.suffix.lower()
    if ext == ".py":
        return extract_python_registrations(lines)
    if ext in (".js", ".mjs", ".cjs", ".ts"):
        return extract_ts_registrations(lines)
    return []

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
SEVERITY_LADDER = ["info", "low", "medium", "high"]
EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

KIND_RANK = {"local": 0, "system": 1, "repo": 2}

# Verdict precedence (first match wins).
VERDICT_PRECEDENCE = [
    ("unverifiable", "S002"),
    ("unverifiable", "S101"),
    ("unverifiable", "S102"),
    ("purpose-mismatch", "P101"),
    ("excessive-authority", "P103"),
]


# =============================================================================
# Capability catalog: observed behavior vs declared vocabulary
#
# Patterns are searched over the WHOLE file (frontmatter included - smuggling
# instructions into `description:` is a known trick). A category is DECLARED
# when any of its lexicon words appears in the claimed purpose text; every
# OBSERVED-but-not-declared category becomes a P101 finding.
# =============================================================================

CAPABILITY_CATEGORIES = {
    "secrets-access": {
        "severity": "high",
        "lexicon": [
            "secret", "credential", "password", "passphrase", "api key",
            "apikey", "token", "ssh", "gpg", "pgp", "keystore", "keychain",
            "wallet", ".env", "signing", "oauth", "auth",
        ],
        "patterns": [
            re.compile(r"\.ssh/id_", re.IGNORECASE),
            re.compile(r"\bid_(rsa|dsa|ecdsa|ed25519)\b", re.IGNORECASE),
            re.compile(r"\.(aws/credentials|npmrc|netrc|git-credentials)\b", re.IGNORECASE),
            # .env as a FILE (dotenv conventions), not JavaScript's
            # `process.env` API: the dot must NOT be glued to an identifier
            # or another dot (process.env, os.env, foo..env), so a plain
            # lookbehind for the literal "process." can never work - the
            # match's own dot consumes the separator.
            re.compile(r"(?<![\w.])\.env\b(?!\s*(example|sample|template))", re.IGNORECASE),
            re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
            re.compile(r"wallet\.dat|seed phrase|recovery phrase|metamask", re.IGNORECASE),
            re.compile(
                r"\b(cat|read|open|type|copy|grab|exfiltrate|steal)\b[^\n]{0,40}"
                r"\b(passwords?|credentials|api[- ]?keys?|tokens?|private keys?)\b",
                re.IGNORECASE,
            ),
        ],
    },
    "network-egress": {
        "severity": "high",
        "lexicon": [
            "fetch", "http", "https", "url", "api", "web", "download",
            "upload", "network", "request", "curl", "wget", "webhook",
            "endpoint", "internet", "remote", "post", "send", "sync",
            "telemetry", "online",
        ],
        "patterns": [
            re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b", re.IGNORECASE),
            re.compile(r"\biex\s*\(\s*(iwr|invoke-webrequest|new-object)", re.IGNORECASE),
            re.compile(r"\bnc\b[^#\n]*\s-[le]\b|/dev/tcp/", re.IGNORECASE),
            re.compile(
                r"hooks\.slack\.com|discord(app)?\.com/api/webhooks|pastebin\.com/raw"
                r"|webhook\.site|ngrok(\.io|-free)\.|pipedream\.net|requestbin|interact\.sh",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(send|upload|post|transmit|forward|exfiltrate)\b[^\n]{0,60}https?://",
                re.IGNORECASE,
            ),
        ],
    },
    "destructive-commands": {
        "severity": "medium",
        "lexicon": [
            "delete", "remove", "clean", "cleanup", "reset", "wipe", "force",
            "purge", "drop", "truncate", "migrate", "rollback", "uninstall",
            "teardown",
        ],
        "patterns": [
            # Destructive means BROAD deletion: recursive (-r/-R present).
            # A bare `rm -f out.docx` before re-zipping is ordinary build
            # hygiene and must not read as a destructive capability.
            re.compile(r"\brm\s+-[a-zA-Z]*[rR]", re.IGNORECASE),
            re.compile(r"\bdel\s+/[sq]\b|\brmdir\s+/s\b", re.IGNORECASE),
            re.compile(r"\bgit\s+push\s+(--force\b(?!-with-lease)|-\w*f)", re.IGNORECASE),
            re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
            re.compile(r"\bDROP\s+(TABLE|DATABASE)\b|\bTRUNCATE\s+TABLE\b", re.IGNORECASE),
            re.compile(r"\bchmod\s+-R?f?\s*777\b|\bmkfs\.|\bwipefs\b|\bdd\s+if=[^\n]*of=/dev/", re.IGNORECASE),
        ],
    },
    "process-execution": {
        "severity": "medium",
        "lexicon": [
            "run", "execute", "exec", "command", "script", "shell",
            "terminal", "install", "build", "compile", "invoke", "spawn",
            "automation", "pipeline",
        ],
        "patterns": [
            re.compile(r"\beval\s*\("),
            re.compile(r"\bos\.system\s*\(|subprocess\.(run|call|check_output|Popen)\b"),
            re.compile(r"\bchild_process\b|\bexec(?:Sync)?\s*\("),
            re.compile(r"\b(ba|z|da|k)?sh\s+-c\s+[\"']", re.IGNORECASE),
            re.compile(r"-enc(odedcommand)?\b|\bInvoke-Expression\b", re.IGNORECASE),
        ],
    },
    "persistence-hooks": {
        "severity": "high",
        "lexicon": [
            "persist", "startup", "login", "schedule", "cron", "hook",
            "autostart", "boot", "daemon", "service", "registry",
        ],
        "patterns": [
            re.compile(r"\bcrontab\b|\bschtasks\b|\blaunchctl\b", re.IGNORECASE),
            re.compile(r"~/\.(bashrc|zshrc|profile|bash_profile)"),
            re.compile(r"authorized_keys"),
            re.compile(r"HKCU[\\/].{0,40}[\\/]Run\b", re.IGNORECASE),
            re.compile(r"systemd[^\n]{0,30}\.service", re.IGNORECASE),
        ],
    },
}

# =============================================================================
# Capability evidence in BUNDLED EXECUTABLE FILES. Each pattern carries a
# weight: 'strong' matches stand on their own at the category's full
# severity, while a category evidenced ONLY by 'weak' matches is downgraded
# one level - plain outbound HTTP is ordinary tooling, pipe-to-shell is not.
# =============================================================================

SCRIPT_CAPABILITY_PATTERNS = {
    "secrets-access": [
        (re.compile(r"(?<![\w.])\.env\b(?![-\w])"), "strong"),
        (re.compile(r"\bid_(rsa|dsa|ecdsa|ed25519)\b", re.IGNORECASE), "strong"),
        (re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"), "strong"),
        (re.compile(r"\.(aws[/\\]credentials|ssh/id_|gnupg/)"), "strong"),
    ],
    "network-egress": [
        (re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b", re.IGNORECASE), "strong"),
        (re.compile(
            r"hooks\.slack\.com|discord(app)?\.com/api/webhooks|pastebin\.com/raw"
            r"|webhook\.site|ngrok(\.io|-free)\.|pipedream\.net|requestbin|interact\.sh",
            re.IGNORECASE), "strong"),
        (re.compile(r"/dev/tcp/|\bnc\b[^#\n]*\s-[le]\b", re.IGNORECASE), "strong"),
        # Deliberately NO bare-URL pattern here: URLs also appear inside XML
        # namespaces and HTML templates that scripts merely GENERATE, which
        # is not egress. Only actual call-forms count as weak evidence.
        (re.compile(
            r"\b(?:await\s+)?fetch\s*\(|axios(?:\.(?:get|post|put|delete|request))?[\s(.]"
            r"|requests\.(?:get|post|put)|urlopen\s*\(|https?\.request",
            re.IGNORECASE), "weak"),
    ],
    "destructive-commands": [
        (re.compile(r"(rmSync|rmdirSync)\([^)]*recursive\s*:\s*true", re.IGNORECASE), "strong"),
        (re.compile(r"\brimraf\b|shutil\.rmtree\s*\("), "strong"),
        (re.compile(r"os\.system\(\s*[\"']rm\s+-[a-zA-Z]*r", re.IGNORECASE), "strong"),
    ],
    "process-execution": [
        (re.compile(r"\beval\s*\(|new Function\s*\(|Invoke-Expression\b", re.IGNORECASE), "strong"),
        (re.compile(r"child_process\s*\.\s*exec\b|execSync\s*\("), "strong"),
        (re.compile(r"shell\s*=\s*True|\bbash\s+-c\b|\bpowershell\b[^\n]*-Enc", re.IGNORECASE), "strong"),
        # structured/list-form spawns are ordinary tooling -> weak
        (re.compile(r"\bsubprocess\.(run|Popen|check_output|call)\b"), "weak"),
        (re.compile(r"child_process\s*\.\s*(spawn|spawnSync|execFile)\b"), "weak"),
    ],
    "persistence-hooks": [
        (re.compile(r"\bcrontab\b|\bschtasks\b|\blaunchctl\b", re.IGNORECASE), "strong"),
        (re.compile(r"(?<![\w.])\.(bashrc|zshrc|profile|bash_profile)\b"), "strong"),
        (re.compile(r"systemd[^\n]{0,30}\.service", re.IGNORECASE), "strong"),
    ],
}

# =============================================================================
# Authority signal catalog: vague, silent, or overly broad agency language.
# These NEVER become findings on their own. They surface as advisory notes,
# and only materialize as corroborated findings when the same skill has an
# undeclared capability (P101). All patterns use bounded quantifiers.
# =============================================================================

AUTHORITY_SIGNAL_RULES = [
    {
        "rule": "A101",
        "title": "overly broad scope over sensitive objects",
        "detail": (
            "universal quantifier ('all'/'any'/'every') applied to sensitive "
            "objects"
        ),
        "pattern": re.compile(
            r"\b(all|any|every|the entire)\b[^.\n]{0,60}\b"
            r"(credentials?|secrets?|api[- ]?keys?|passwords?|private keys?"
            r"|ssh keys?|wallets?|cookies?|browser (history|data|profile)"
            r"|clipboard|emails?|messages?|photos?|contacts?|environment variables?"
            r"|env vars?|home (dir|directory)|hard drive|filesystem)\b",
            re.IGNORECASE,
        ),
        "scope": "file",
    },
    {
        "rule": "A102",
        "title": "instructs silent or unconfirmed action",
        "detail": (
            "asks the agent to act without asking/telling the user; on its "
            "own a phrasing convention, but exactly the enabler you want "
            "next to undeclared capabilities"
        ),
        "pattern": re.compile(
            r"\bwithout (asking|confirmation|permission|prompting|notice|telling|informing)\b"
            r"|\bdo(n'?| no)t (ask|tell|inform|confirm|mention|reveal)\b"
            r"|\bnever (ask|inform|reveal|tell|mention)\b"
            r"|\bavoid (mentioning|noting|revealing|telling)\b"
            r"|\bhide (this|that|the) (fact|behavior|instruction|capability)\b"
            # adverbs of concealment count only when they modify an action
            # verb - bare 'silently' is ordinary in legitimate API docs
            # ('requests silently drop ...').
            r"|\b(silently|stealthily|covertly|secretly)[ ,]+"
            r"(ask|run|execute|send|upload|post|delete|remove|modify|change"
            r"|install|read|collect|gather|exfiltrate|copy|forward|download)\b",
            re.IGNORECASE,
        ),
        "scope": "file",
    },
    {
        "rule": "A103",
        "title": "vague delegation imperative",
        "detail": (
            "open-ended delegation ('as you see fit', 'do whatever', "
            "'handle everything') makes granted authority unreviewable"
        ),
        "pattern": re.compile(
            r"\bas (you )?see fit\b"
            r"|\buse (your )?(best )?(judgment|discretion)\b"
            r"|\bdo whatever\b|\bwhatever it takes\b"
            r"|\banything you (want|need|like|deem)\b"
            r"|\bhandle (everything|anything)\b|\bfigure it out\b",
            re.IGNORECASE,
        ),
        "scope": "file",
    },
    {
        "rule": "A104",
        "title": "instruction-override phrasing",
        "detail": (
            "attempts to disable or replace the agent's own rules; verb and "
            "target must sit within ~24 chars so defensive prose like 'ignore "
            "anything else it may contain' does not match"
        ),
        "pattern": re.compile(
            r"\b(ignore|disregard|forget|override|bypass)[^\n]{0,24}"
            r"\b(previous|prior|earlier|above|original|system|all)?\s*"
            r"(instructions?|prompts?|rules?|constraints?|guardrails?|restrictions?"
            r"|policies?|safeguards?)\b"
            r"|\byou (now )?(have|are) no (restrictions|limits|rules|filters|guidelines)\b"
            r"|\bact as (an? )?(unrestricted|uncensored|unfiltered|jailbroken|dan)\b"
            r"|\bDAN mode\b|\bdeveloper mode\b|\bdo anything now\b",
            re.IGNORECASE,
        ),
        "scope": "file",
    },
    {
        "rule": "A105",
        "title": "encoded blob embedded in prose",
        "detail": (
            "long base64-like or hex run; payloads hide from review behind "
            "encoding"
        ),
        "pattern": re.compile(r"[A-Za-z0-9+/]{100,}={0,2}|\b[0-9a-f]{200,}\b"),
        "scope": "body",
    },
]

INVISIBLE_CHARS = {
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE (BOM)",
    "\u00ad": "SOFT HYPHEN",
    "\u180e": "MONGOLIAN VOWEL SEPARATOR",
}
INVISIBLE_RE = re.compile("[" + "".join(INVISIBLE_CHARS.keys()) + "]")

STOPWORDS = frozenset("""
a an the and or but if then else for with from into onto when while your
their his her its our my me you he she it we they them this that these those
be been being am is are was were will would shall should can could may might
must also only just very more most than then too there here about after before
between through during without within along across because which whose what
who whom how why not no nor so such own same some any each every both few many
much other another uses used using user users make makes made want wants likes
need needs able helps helping allow allows providing provide provides ensure
ensures given based including include includes work works working follow
follows following take takes taken get gets got go goes going done does doing
""".split())


# =============================================================================
# Small deterministic helpers
# =============================================================================

def trim_quote(text: str) -> str:
    q = " ".join(text.split())
    if len(q) > QUOTE_MAX_CHARS:
        q = q[: QUOTE_MAX_CHARS - 3] + "..."
    return q


def make_finding(rule, severity, title, line, quote, detail, **extra):
    f = {
        "rule": rule,
        "severity": severity,
        "title": title,
        "line": line,
        "quote": trim_quote(quote),
        "detail": detail,
    }
    f.update(extra)
    f["fingerprint"] = finding_fingerprint(f)
    return f


def finding_fingerprint(finding):
    """Stable identity of a finding across runs: rule + normalized quote.
    Deliberately excludes path (temp clone dirs differ) and line numbers
    (they drift when files are edited)."""
    digest = hashlib.sha256(
        f"{finding['rule']}\x00{finding['quote']}".encode("utf-8")
    ).hexdigest()
    return digest[:16]


def sort_findings(findings):
    return sorted(
        findings,
        key=lambda f: (f["line"], SEVERITY_ORDER[f["severity"]], f["rule"]),
    )


def cap_per_rule(findings):
    """Keep at most MAX_FINDINGS_PER_RULE findings per rule id (first by line)."""
    counts = {}
    kept = []
    for f in sort_findings(findings):
        n = counts.get(f["rule"], 0)
        if n >= MAX_FINDINGS_PER_RULE:
            continue
        counts[f["rule"]] = n + 1
        kept.append(f)
    return kept


# =============================================================================
# Frontmatter parsing (deliberate YAML subset - stdlib only, deterministic)
# =============================================================================

KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")
# Real-world agent/command frontmatter uses exotic-but-valid YAML keys
# (opencode agents carry a tools mapping with a quoted "*" wildcard key).
# Tolerate quoted/star keys so valid definitions are not branded unverifiable.
QUOTED_KEY_RE = re.compile(r'^(?:\"([^\"]+)\"|\'([^\']+)\')\s*:\s*(.*)$')
STAR_KEY_RE = re.compile(r'^\*\s*:\s*(.*)$')
BLOCK_STYLES = {">", ">-", ">+", "|", "|-", "|+"}


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        inner = v[1:-1]
        if v[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        else:
            inner = inner.replace("''", "'")
        return inner.strip()
    return v


def parse_frontmatter(lines):
    """Parse the leading `---` fenced YAML subset.

    Returns dict: present, closed, meta ({key: {value, line}}), errors
    ([{line, text}]), body_start (1-based).
    """
    out = {"present": False, "closed": False, "meta": {}, "errors": [], "body_start": 1}
    if not lines or lines[0].strip().lstrip("\ufeff").strip() != "---":
        return out
    out["present"] = True
    meta = {}
    errors = []
    i = 1
    n_lines = len(lines)

    def flush_block(key, style, buf, start_line):
        if key is None:
            return
        indents = [len(l) - len(l.lstrip()) for l in buf if l.strip()]
        dedent = min(indents) if indents else 0
        chunks = [(l[dedent:] if len(l) >= dedent else l.lstrip()).rstrip() for l in buf]
        if style.startswith(">"):
            value = " ".join(c for c in chunks if c != "")
        else:
            value = "\n".join(chunks).strip("\n")
        meta[key] = {"value": value.strip(), "line": start_line}

    key = None
    block_buf = []
    block_style = ""
    block_line = 0
    while i < n_lines:
        raw = lines[i]
        stripped = raw.strip()
        if stripped in ("---", "..."):
            out["closed"] = True
            out["body_start"] = i + 1
            i += 1
            break
        if key is not None:
            # Inside a block scalar: consume indented/blank lines only.
            if stripped == "" or raw[:1] in (" ", "\t"):
                block_buf.append(raw)
                i += 1
                continue
            flush_block(key, block_style, block_buf, block_line)
            key = None
            # fall through to parse the current line as a normal line
        if stripped == "" or stripped.startswith("#"):
            i += 1
            continue
        # Key matching runs on the STRIPPED line: real-world frontmatter
        # nests indented mappings (opencode agents: tools -> "*" : false)
        # and an indent must not turn a valid key into a parse error.
        probe = raw.strip()
        m = KEY_LINE_RE.match(probe)
        if m:
            k, v = m.group(1), m.group(2).strip()
        else:
            qm = QUOTED_KEY_RE.match(probe)
            if qm:
                k = qm.group(1) or qm.group(2)
                v = qm.group(3).strip()
            else:
                sm = STAR_KEY_RE.match(probe)
                if not sm:
                    if raw.lstrip().startswith("- "):
                        # sequence item of a mapping we flattened - harmless
                        i += 1
                        continue
                    errors.append({"line": i + 1, "text": trim_quote(raw)})
                    i += 1
                    continue
                k, v = "*", sm.group(1).strip()
        if v in BLOCK_STYLES:
            key, block_style, block_buf, block_line = k, v, [], i + 1
            i += 1
            continue
        meta[k.lower()] = {"value": _unquote(v), "line": i + 1}
        i += 1
    if key is not None:
        flush_block(key, block_style, block_buf, block_line)
    norm = {}
    for k, val in meta.items():
        norm[k.lower()] = val
    out["meta"] = norm
    out["errors"] = errors
    if not out["closed"]:
        out["body_start"] = min(out["body_start"], n_lines + 1)
    return out


# =============================================================================
# Discovery
# =============================================================================

class RootStats:
    def __init__(self, kind, given_path, exists):
        self.kind = kind
        self.given_path = given_path
        self.exists = exists
        self.files_seen = 0
        self.candidates = 0
        self.skipped_binary = 0
        self.skipped_large = []
        self.notes = []
        self.truncated = False

    def to_json(self):
        return {
            "kind": self.kind,
            "path": self.given_path,
            "exists": self.exists,
            "files_seen": self.files_seen,
            "files_considered": self.candidates,
            "skipped_binary": self.skipped_binary,
            "skipped_large": self.skipped_large,
            "truncated": self.truncated,
            "notes": self.notes,
        }


def walk_candidates(root: Path, stats: RootStats):
    """Return sorted [(rel_posix, abs_path)] markdown-ish candidates."""

    def consider(p: Path, rel_parts):
        rel = "/".join(rel_parts)
        name = p.name
        if name == "SKILL.md" or p.suffix.lower() in (".md", ".mdc"):
            stats.candidates += 1
            found.append((rel, p))

    found = []

    def rec(d: Path, rel_parts, depth):
        if depth > MAX_DEPTH:
            stats.notes.append(f"path exceeds MAX_DEPTH ({MAX_DEPTH}): {'/'.join(rel_parts)}")
            return
        try:
            entries = sorted(d.iterdir(), key=lambda e: e.name)
        except OSError as exc:
            stats.notes.append(f"unreadable directory {'/'.join(rel_parts) or '.'}: {exc.strerror or exc}")
            return
        for e in entries:
            if len(found) >= MAX_FILES_PER_ROOT:
                stats.truncated = True
                return
            if e.is_symlink():
                stats.notes.append(f"skipped symlink: {'/'.join(rel_parts + [e.name])}")
                continue
            if e.is_dir():
                if e.name in DISCOVERY_SKIP_DIRS:
                    continue
                rec(e, rel_parts + [e.name], depth + 1)
                if len(found) >= MAX_FILES_PER_ROOT:
                    stats.truncated = True
                    return
            elif e.is_file():
                stats.files_seen += 1
                if e.name.startswith("."):
                    continue
                try:
                    if e.stat().st_size > MAX_FILE_BYTES:
                        stats.skipped_large.append("/".join(rel_parts + [e.name]))
                        continue
                except OSError:
                    continue
                consider(e, rel_parts + [e.name])

    if root.is_file():
        consider(root, [root.name])
        return sorted(dict(found).items(), key=lambda t: t[0])

    rec(root, [], 1)
    uniq = {}
    for rel, p in found:
        uniq.setdefault(rel, p)
    return sorted(uniq.items(), key=lambda t: t[0])


CARD_STORAGE_DIRS = ("skills", "rules")
DEFINITION_STORAGE_DIRS = CARD_STORAGE_DIRS + ("agents", "commands")


def is_card_by_location(rel_posix: str, root_name: str = "") -> bool:
    """Location alone makes a markdown file a capability CLAIM only when its
    IMMEDIATE parent directory is a known storage directory:

      skills/   skill cards          rules/    rule files
      agents/   agent definitions    commands/ slash-command definitions

    or when the file sits directly inside a scan ROOT named skills/rules.

    Two deliberate exclusions:
      - Inside a skill BUNDLE (a `skills` directory above the immediate
        parent - e.g. skills/<name>/rules/x.md) only the bundle's own
        SKILL.md is the claim; everything else is reference material unless
        it carries explicit name+description frontmatter.
      - Files merely having a skills ancestor further up are likewise bundle
        content, not claims."""
    parts = PurePosixPath(rel_posix).parts
    parents = parts[:-1]
    outer = parents[:-1]
    if any(p.lower() == "skills" for p in outer):
        return False
    if not parents:
        return root_name.lower() in CARD_STORAGE_DIRS
    return parents[-1].lower() in DEFINITION_STORAGE_DIRS


def definition_format(rel_posix: str) -> str:
    """Refine the 'card' format using the storage directory: agent/command
    definitions carry a description-only contract (the name comes from the
    file), which their runtimes load deliberately - so they deserve their own
    label instead of being folded into generic skills."""
    parent = PurePosixPath(rel_posix).parts[-2:-1]
    if parent and parent[0].lower() == "agents":
        return "agent"
    if parent and parent[0].lower() == "commands":
        return "command"
    return "card"


class Unit:
    __slots__ = ("format", "root_kind", "display_path", "meta", "fm", "lines",
                 "body_start", "abs_dir")

    def __init__(self, fmt, root_kind, display_path, fm, lines, abs_dir=None):
        self.format = fmt
        self.root_kind = root_kind
        self.display_path = display_path
        self.fm = fm
        self.lines = lines
        self.body_start = fm["body_start"]
        # Real directory of a SKILL.md bundle (None for standalone cards):
        # bundled executable files are behavior evidence for THIS contract.
        self.abs_dir = abs_dir

    def field(self, key):
        return self.fm["meta"].get(key)

    def text_value(self, key):
        f = self.field(key)
        return f["value"] if f else ""

    @property
    def name(self):
        explicit = self.field("name")
        if explicit and explicit["value"].strip():
            return explicit["value"].strip()
        if self.format == "claude":
            parent = PurePosixPath(self.display_path).parent
            return parent.name if parent.name not in ("", "/", ".") else "unnamed"
        stem = PurePosixPath(self.display_path).stem
        return stem or "unnamed"

    @property
    def name_line(self):
        explicit = self.field("name")
        if explicit:
            return explicit["line"]
        return 1

    @property
    def description(self):
        d = self.field("description")
        return d["value"] if d else ""

    def numbered_body(self):
        return [
            (self.body_start + offset, ln)
            for offset, ln in enumerate(self.lines[self.body_start - 1:])
        ]

    def claimed_purpose_text(self):
        parts = [self.name]
        for key in ("description", "trigger", "triggers", "trigger_phrase",
                    "when_to_use", "use_when", "tags"):
            parts.append(self.text_value(key))
        return " ".join(parts)


# =============================================================================
# Agent/command adapter: declared TOOL surface from frontmatter.
# Claude Code commands declare `allowed-tools:`; opencode agents declare a
# `tools:` mapping (our frontmatter flattener surfaces its entries as
# top-level keys). A declared tool maps to a capability category through
# this table - the agent-world sibling of MCP's mcpAllowlist handling.
# File-operation tools (Read/Write/Edit/Grep/Glob) map to no v1 category.
# =============================================================================

DECLARED_TOOL_KINDS = {
    "bash": "process-execution",
    "shell": "process-execution",
    "sh": "process-execution",
    "exec": "process-execution",
    "run": "process-execution",
    "cmd": "process-execution",
    "terminal": "process-execution",
    "webfetch": "network-egress",
    "websearch": "network-egress",
    "http": "network-egress",
    "fetch": "network-egress",
    "curl": "network-egress",
    "wget": "network-egress",
}
DECLARED_TOOL_NEUTRAL = frozenset((
    "read", "write", "edit", "grep", "glob", "list", "view", "todo"))
DISABLED_VALUES = frozenset(("false", "no", "off", "0", ""))


def extract_declared_tools(unit):
    """-> sorted unique declared tool names for agent/command units.

    Sources: `allowed-tools:` value(s) and a `tools:` value, plus flattened
    mapping keys whose names are recognizable tools. Entries explicitly
    disabled in the source mapping (webfetch: false) are skipped."""
    names = set()
    for key in ("allowed-tools", "tools"):
        field = unit.field(key)
        if field and field["value"].strip():
            for tok in re.split(r"[,\[\]{}]+", field["value"]):
                if tok.strip():
                    names.add(tok.strip())
    for key, field in unit.fm["meta"].items():
        base = key.split("(")[0].strip().lower()
        if base in DECLARED_TOOL_KINDS or base in DECLARED_TOOL_NEUTRAL:
            if str(field.get("value", "")).strip().lower() \
                    in DISABLED_VALUES and key != key.upper():
                continue
            names.add(key.strip())
    normalized = set()
    for n in names:
        m = re.match(r"([A-Za-z][A-Za-z0-9_-]*)", n)
        if m:
            candidate = m.group(1)
            low = candidate.lower()
            if low in DECLARED_TOOL_KINDS or low in DECLARED_TOOL_NEUTRAL:
                normalized.add(candidate)
    return sorted(normalized, key=str.lower)


def declared_tool_categories(tools):
    kinds = set()
    for name in tools:
        kind = DECLARED_TOOL_KINDS.get(name.lower())
        if kind:
            kinds.add(kind)
    return sorted(kinds)


def build_unit(root_kind, display_root, rel, path: Path):
    """Create a Unit for one candidate file, or None/'binary'."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return "binary"
    # A real UTF-8 BOM decodes to \ufeff; strip once so it is never mistaken
    # for hidden-character smuggling (A106) on line 1.
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    lines = text.splitlines()
    display = f"{display_root}/{rel}" if display_root != "." else rel
    fm = parse_frontmatter(lines)
    fmt = "claude" if path.name == "SKILL.md" else "card"
    abs_dir = path.parent if fmt == "claude" else None
    return Unit(fmt, root_kind, display, fm, lines, abs_dir)


# =============================================================================
# Structural analysis (S-rules) - always findings
# =============================================================================

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def analyze_structure(unit: Unit, seen_names):
    """Returns (findings, fields_trustworthy)."""
    findings = []
    fm = unit.fm
    if not fm["present"]:
        findings.append(make_finding(
            "S001", "medium", "skill file has no frontmatter",
            1, unit.lines[0] if unit.lines else "",
            "agent runtimes route skills via frontmatter fields; without it "
            "the file is inert documentation at best"))
        return findings, False
    if not fm["closed"] or fm["errors"]:
        first_err = fm["errors"][0] if fm["errors"] else None
        findings.append(make_finding(
            "S002", "high", "malformed frontmatter",
            first_err["line"] if first_err else 1,
            first_err["text"] if first_err else "",
            "unclosed '---' fence or unparsable line - most runtimes will "
            "silently ignore or misroute this skill; field checks are "
            "suppressed because parsing cannot be trusted, so no contract "
            "can be verified either"))
        return findings, False

    desc = unit.field("description")
    if not desc or not desc["value"].strip():
        findings.append(make_finding(
            "S003", "high", "missing or empty description",
            1, "",
            "`description` decides when a runtime loads this skill and is "
            "half of the contract this tool verifies; an empty one means "
            "either never loaded or always guessed wrong"))
    elif len(desc["value"]) > DESCRIPTION_MAX_CHARS:
        findings.append(make_finding(
            "S007", "medium", "description exceeds the length budget",
            desc["line"],
            desc["value"][:120] + "...",
            f"description is {len(desc['value'])} chars (limit "
            f"{DESCRIPTION_MAX_CHARS}); runtimes truncate or reject it"))

    name_field = unit.field("name")
    derived = name_field is None
    name_val = unit.name
    if not NAME_RE.match(name_val):
        findings.append(make_finding(
            "S004", "medium", "invalid skill name",
            unit.name_line,
            (name_field["value"] if name_field else name_val),
            ("derived from the directory name" if derived else "declared in frontmatter")
            + " - expected ^[a-z0-9][a-z0-9_-]{0,63}$; runtimes may refuse "
              "or normalize it unpredictably"))

    body_len = sum(len(ln) for _, ln in unit.numbered_body())
    if body_len < BODY_EMPTY_CHARS:
        findings.append(make_finding(
            "S005", "info", "empty body",
            max(fm["body_start"], 1), "",
            "frontmatter only - there is no protocol/instruction content to "
            "review or execute"))

    key = name_val.lower()
    if key in seen_names:
        findings.append(make_finding(
            "S006", "medium", "duplicate skill name in scan scope",
            unit.name_line, name_val,
            f"'{name_val}' already claimed by {seen_names[key]}; later "
            "duplicates shadow or collide depending on runtime"))
    else:
        seen_names[key] = unit.display_path
    return findings, True


# =============================================================================
# Contract analysis: observed behavior vs declared purpose
# =============================================================================

def collect_bundle_scripts(unit: Unit):
    """Executable files inside a SKILL.md bundle, deterministic order:
    [(rel_posix, Path)]. Bounded by depth, count and size; symlinks and
    dependency/vendor directories are skipped."""
    if unit.format != "claude" or unit.abs_dir is None:
        return []
    found = []

    def rec(d: Path, rel_parts, depth):
        if depth > MAX_SCRIPT_DEPTH or len(found) >= MAX_SCRIPTS_PER_SKILL:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if len(found) >= MAX_SCRIPTS_PER_SKILL:
                return
            if e.is_symlink():
                continue
            if e.is_dir():
                if e.name in DISCOVERY_SKIP_DIRS:
                    continue
                rec(e, rel_parts + [e.name], depth + 1)
            elif e.is_file():
                if e.suffix.lower() not in SCRIPT_EXTENSIONS:
                    continue
                try:
                    if e.stat().st_size > MAX_SCRIPT_BYTES:
                        continue
                except OSError:
                    continue
                found.append(("/".join(rel_parts + [e.name]), e))

    rec(unit.abs_dir, [], 1)
    return sorted(dict(found).items(), key=lambda t: t[0])


def observe_capabilities(unit: Unit):
    """{category: [evidence]} over the whole file AND bundled scripts.

    Evidence entry: {"src": None|rel_posix, "line": int, "text": str,
    "strong": bool}. Main-file matches keep the prose patterns' full weight;
    script matches carry their pattern's declared weight."""
    evidence_all = {c: [] for c in CAPABILITY_CATEGORIES}

    for lineno, ln in enumerate(unit.lines, start=1):
        for category, spec in CAPABILITY_CATEGORIES.items():
            if any(p.search(ln) for p in spec["patterns"]):
                evidence_all[category].append(
                    {"src": None, "line": lineno, "text": ln, "strong": True})

    script_lines = []
    for rel, spath in collect_bundle_scripts(unit):
        try:
            raw = spath.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        script_lines.append((rel, list(enumerate(text.splitlines(), start=1))))

    for rel, slines in script_lines:
        for lineno, ln in slines:
            for category, spats in SCRIPT_CAPABILITY_PATTERNS.items():
                strong = False
                hit = False
                for pat, weight in spats:
                    if pat.search(ln):
                        hit = True
                        if weight == "strong":
                            strong = True
                            break
                if hit:
                    evidence_all[category].append(
                        {"src": rel, "line": lineno, "text": ln,
                         "strong": strong or False})

    observed = {}
    for category, evs in evidence_all.items():
        if evs:
            observed[category] = sorted(
                evs, key=lambda e: (e["src"] or "", e["line"]))
    return observed


def declared_categories(claim_lower: str, observed):
    """Categories whose vocabulary appears in the claimed purpose text."""
    declared = set()
    for category in observed:
        lexicon = CAPABILITY_CATEGORIES[category]["lexicon"]
        if any(word in claim_lower for word in lexicon):
            declared.add(category)
    return declared


def build_adapter_result(unit: Unit):
    """ADAPTER LAYER OUTPUT - everything the core may consume, already in
    artifact shape (usb-contract-artifacts/v1).

    Keys prefixed with "_" are process-local inputs for rules whose inputs
    are not yet part of the public schema; they are never serialized.
    Everything else is byte-stable and covered by the golden snapshot."""
    signals = collect_authority_signals(unit)
    observed = observe_capabilities(unit)
    claim = unit.claimed_purpose_text()

    # agent/command adapters: explicit tool declarations join the claim
    # text and map to declared categories via DECLARED_TOOL_KINDS - the
    # agent-world sibling of MCP's mcpAllowlist handling
    declared_tools = []
    if unit.format in ("agent", "command"):
        declared_tools = extract_declared_tools(unit)
        tool_words = " ".join(declared_tools)
        if tool_words:
            claim = f"{claim} {tool_words}".strip()
    tools_declared = bool(declared_tools)

    declared = declared_categories(claim.lower(), observed)
    for kind in declared_tool_categories(declared_tools):
        declared.add(kind)
    return {
        "identity": {
            "name": unit.name,
            "kind": unit.format,
            "source_kind": unit.root_kind,
            "source": unit.display_path,
        },
        "declared": {
            "text": trim_quote(claim),
            "categories": sorted(declared),
            "description_chars": len(unit.description),
            "tools_declared": tools_declared,
            "tools": [{"name": t, "description": ""}
                      for t in declared_tools],
        },
        "observed_capabilities": [
            {
                "category": cat,
                "strength": "strong" if e["strong"] else "weak",
                "source": e["src"],
                "line": e["line"],
                "text": trim_quote(e["text"]),
            }
            for cat, evs in sorted(observed.items())
            for e in evs
        ],
        "authority_scope": {
            "signals": [
                {
                    "rule": s["rule"],
                    "title": s["title"],
                    "line": s["line"],
                    "matched": s["matched"],
                    "quote": s["quote"],
                }
                for s in signals
            ],
            # v1 scope summary for skills: the bundle surface it can act on.
            "bundle_scripts": sorted(
                rel for rel, _ in collect_bundle_scripts(unit)
            ) if unit.format == "claude" else [],
        },
        "_observed_map": observed,
        "_signals": signals,
        "_context": {
            "lines": unit.lines,
            "description_value": unit.description,
            "description_line": (unit.field("description") or {})
            .get("line", 1),
        },
    }


def _downgrade(severity):
    idx = SEVERITY_LADDER.index(severity)
    return SEVERITY_LADDER[max(idx - 1, 1)]  # never below "low"


def p101_findings(art):
    """CORE: undeclared capability = the contract violation itself. Consumes
    ONLY the adapter artifact - evidence may come from prose OR bundled
    scripts; a category evidenced only by weak script signals is downgraded
    one severity level."""
    observed = art["_observed_map"]
    declared = set(art["declared"]["categories"])
    authority_signals = art["authority_scope"]["signals"]
    findings = []
    undeclared = sorted(set(observed) - set(declared))
    for category in undeclared:
        spec = CAPABILITY_CATEGORIES[category]
        evidence = observed[category]
        any_strong = any(e["strong"] for e in evidence)
        severity = spec["severity"] if any_strong else _downgrade(spec["severity"])
        first = evidence[0]
        detail = (
            f"{len(evidence)} signal line(s) show {category}, but the "
            "declared contract (name/description/trigger) never covers it. "
            "Either declare the capability or remove the behavior."
        )
        if not any_strong:
            detail += " Weak script-level signals only - severity downgraded."
        corrob = [s for s in authority_signals if s["line"] <= first["line"] + 40]
        if corrob:
            cites = "; ".join(
                f"{s['rule']}@{s['line']}" for s in corrob[:CORROBORATION_DETAIL_CAP])
            detail += f" Corroborating authority signals: {cites}."
        extra = {}
        if first["src"]:
            extra["evidence_path"] = first["src"]
        findings.append(make_finding(
            "P101", severity,
            f"undeclared capability: {category}",
            first["line"], first["text"], detail, **extra))
    return findings


def p102_finding(art):
    """CORE: description promises words that appear nowhere in the unit.
    Consumes the adapter's process-local context (description + body text);
    P102 is not yet reconstructible from the public artifact schema, so it
    stays engine-side until the spec extends."""
    desc_value = art["_context"]["description_value"]
    lines = art["_context"]["lines"]
    if not desc_value or not desc_value.strip():
        return []
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", desc_value):
        lw = w.lower().strip("'-")
        if len(lw) >= 4 and lw not in STOPWORDS and lw not in words:
            words.append(lw)
        if len(words) >= 30:
            break
    if len(words) < 6:
        return []
    corpus = "\n".join(lines).lower()
    if any(w in corpus for w in words):
        return []
    return [make_finding(
        "P102", "low", "unsupported purpose statement",
        art["_context"]["description_line"], desc_value,
        "none of the description's characteristic words appear in the body "
        "- the declared purpose has no textual basis in the actual "
        "instructions")]


def _authority_item_tokens(item: str):
    return [p.lower() for p in re.split(r"[^A-Za-z0-9]+", item)
            if len(p) >= 3]


def _explained_by_claim(item_tokens, claim_lower: str) -> bool:
    """An authority item explains itself when ANY of its meaningful tokens
    appears in the declared purpose text. Generic tokens were removed by
    the caller; an item reduced to nothing generic-only (e.g. a bare
    'API_KEY') is treated as explained - absence of vocabulary is not
    evidence of breadth."""
    if not item_tokens:
        return True
    return any(t in claim_lower for t in item_tokens)


def p103_finding(art):
    """CORE: authority reach the declared purpose cannot explain.

    Advisory by construction: severity is always 'low', so this never
    gates the exit code on its own (spec section 4.1 - excessive-authority
    is not malice; broad-but-explained authority is ordinary).

    Consumes ONLY serialized artifact fields. v1 judges two sources:
      - credential-descriptor env keys whose domain vocabulary shares
        nothing with the claim (GITHUB_TOKEN on a github server is fine;
        STRIPE_SECRET_KEY on a weather bot is not)
      - filesystem mounts under the same vocabulary test
    Remote transports are deliberately excluded: their egress already
    produces observed evidence that P101 judges. Operator permission rules
    are never judged - they describe what humans allow, not what servers
    are.

    Line anchors at 1 on purpose: the finding aggregates items rather than
    citing one config line, and both engine and independent reducer derive
    identical bytes from the same artifact.
    Returns [] or exactly one finding."""
    claim_lower = art["declared"]["text"].lower()
    scope = art["authority_scope"]
    unexplained = []
    for key in scope.get("env_keys") or []:
        if not CREDENTIAL_DESCRIPTOR_RE.search(key):
            continue
        toks = [t for t in _authority_item_tokens(key)
                if t not in MCP_GENERIC_ITEM_TOKENS]
        if not _explained_by_claim(toks, claim_lower):
            unexplained.append(f"env {key}")
    for mount in scope.get("mounts") or []:
        if FILESYSTEM_PURPOSE_RE.search(claim_lower):
            break  # a filesystem-flavored purpose explains path mounts
        segs = [s for s in _authority_item_tokens(mount)
                if s not in MCP_GENERIC_PATH_SEGMENTS]
        if not _explained_by_claim(segs, claim_lower):
            unexplained.append(f"mount {mount}")
    if not unexplained:
        return []
    detail = (
        f"{len(unexplained)} requested-authority item(s) share no "
        "vocabulary with the declared purpose: "
        + "; ".join(unexplained[:3])
        + ("." if len(unexplained) <= 3 else f" (+{len(unexplained) - 3} more).")
        + " Possible over-breadth - advisory only, this never blocks by "
        "itself.")
    return [make_finding(
        "P103", "low", "authority beyond declared purpose",
        1, trim_quote(unexplained[0]), detail)]


def collect_authority_signals(unit: Unit):
    """Language-level agency signals. Advisory only: never findings alone."""
    signals = []
    numbered_body = unit.numbered_body()

    for spec in AUTHORITY_SIGNAL_RULES:
        scope = numbered_body if spec["scope"] == "body" else enumerate(unit.lines, start=1)
        seen_lines = set()
        for lineno, ln in scope:
            if lineno in seen_lines:
                continue
            m = spec["pattern"].search(ln)
            if m:
                seen_lines.add(lineno)
                signals.append({
                    "rule": spec["rule"],
                    "title": spec["title"],
                    "detail": spec["detail"],
                    "line": lineno,
                    "quote": trim_quote(ln),
                    "matched": trim_quote(m.group(0)),
                })
    # invisible characters, whole file
    for lineno, ln in enumerate(unit.lines, start=1):
        found = INVISIBLE_RE.findall(ln)
        if found:
            cps = ", ".join(f"U+{ord(c):04X} {INVISIBLE_CHARS[c]}"
                            for c in sorted(set(found)))
            signals.append({
                "rule": "A106",
                "title": "invisible characters in instructions",
                "detail": ("hidden text is invisible in review surfaces but "
                           "readable by the model"),
                "line": lineno,
                "quote": trim_quote(ln),
                "matched": cps,
            })
    signals.sort(key=lambda s: (s["line"], s["rule"]))
    # Deterministic cap per rule.
    counts = {}
    kept = []
    for s in signals:
        n = counts.get(s["rule"], 0)
        if n >= MAX_FINDINGS_PER_RULE:
            continue
        counts[s["rule"]] = n + 1
        kept.append(s)
    return kept


def corroborated_signal_findings(authority_signals):
    """When an undeclared capability exists, the signals stop being notes and
    become evidence attached to the report: silent-agency language next to an
    undeclared capability is precisely the abuse pattern."""
    out = []
    for s in authority_signals:
        out.append(make_finding(
            s["rule"], "medium",
            f"authority signal corroborating undeclared capability: {s['title']}",
            s["line"], s["quote"],
            s["detail"] + f" (matched: {s['matched']})",
            corroborated=True))
    return out


# =============================================================================
# MCP adapter (config layer - contract-core depth-order stages 1+2)
#
# One server entry in a well-known JSON config becomes ONE unit whose four
# artifacts follow the same schema as skills:
#   declared   the server NAME plus any EXPLICITLY declared tools. Mainstream
#              runtimes never inline tool lists into server entries, so the
#              only accepted sources are (a) an optional `tools` array on
#              the entry itself and (b) permission-layer allowlists that
#              name tools per server (Cursor's mcpAllowlist). When neither
#              exists nothing is invented: tools_declared=false, and the
#              verdict is untouched - the tool graph simply cannot be
#              compared against a declaration yet (depth stage 3's job).
#              The launch line is behavior, not promise: a command
#              inherently contains its own vocabulary, so treating it as
#              declaration would self-declare everything.
#   observed   capability patterns over command/args/url strings (bundled-
#              script tables reused verbatim - no new taxonomy), plus the
#              bare fact that a remote url reaches the network.
#   authority  requested reach even when never exercised: env var NAMES
#              (requesting a token is ordinary server setup - it is recorded
#              here, never branded behavior), transport types, mounted
#              paths. Feeds excessive-authority reasoning in later depth
#              stages.
#   identity   config-key name; stable locator "<config>#mcpServers/<key>".
#
# A declared-but-unreadable tool list (wrong types, non-string names) is
# S102: the runtime will ignore or reject it, so no tool contract can be
# read - verdict unverifiable.
#
# All evidence anchors at the config line where the server key first appears
# (JSON parsing loses per-field positions; the server block is the honest
# anchor). Fingerprints hash rule+quote, never lines - unaffected.
# =============================================================================

MCP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _categories_for_text(text_lower):
    """Capability categories whose lexicon vocabulary appears in the given
    text - the fresh-text classifier for extracted tool registrations
    (declared_categories() by contrast only re-checks observed ones)."""
    hits = []
    for category, spec in CAPABILITY_CATEGORIES.items():
        if any(word in text_lower for word in spec["lexicon"]):
            hits.append(category)
    return sorted(hits)


class McpUnit:
    __slots__ = ("root_kind", "display_path", "server_name", "_artifact",
                 "_structural")

    format = "mcp-server"

    def __init__(self, root_kind, display_path, server_name=None,
                 artifact=None, structural=None):
        self.root_kind = root_kind
        self.display_path = display_path
        self.server_name = server_name
        self._artifact = artifact
        self._structural = structural or []

    @property
    def name(self):
        return self.server_name or self.display_path

    def claimed_purpose_text(self):
        return self.name

    @property
    def description(self):
        return ""


def find_mcp_configs(root: Path):
    """-> [(rel_posix, Path)] for well-known MCP config files under root.
    Fixed tuple order keeps the walk deterministic."""
    out = []
    if root.is_file():
        if root.name.lower() in MCP_CONFIG_BASENAMES:
            out.append((root.name, root))
        return out
    for rel in MCP_CONFIG_RELPATHS:
        candidate = root / rel
        try:
            if candidate.is_file():
                out.append((rel, candidate))
        except OSError:
            continue
    return out


def normalize_declared_tools(raw):
    """-> (tools, declared, readable).

    tools      [{"name": str, "description": str}] when readable
    declared   True when the entry carries a tools key AT ALL
    readable   False means present but unusable (S102 territory)
    Strings are accepted as name-only declarations; objects must carry a
    string 'name' and may carry a 'description'. Anything else is malformed
    and is NEVER guessed around."""
    if raw is None:
        return [], False, True
    if not isinstance(raw, list):
        return None, True, False
    tools = []
    for entry in raw[:MCP_MAX_TOOLS_DECLARED]:
        if isinstance(entry, str):
            if not entry.strip():
                return None, True, False
            tools.append({"name": entry.strip(), "description": ""})
        elif isinstance(entry, dict):
            name = entry.get("name")
            desc = entry.get("description", "")
            if not isinstance(name, str) or not name.strip() \
                    or not isinstance(desc, str):
                return None, True, False
            tools.append({"name": name.strip(),
                          "description": trim_quote(desc)})
        else:
            return None, True, False
    return tools, True, True


def cursor_mcp_allowlist(root: Path):
    """-> (patterns, structural_finding_or_None) from Cursor's permission
    layer (.cursor/permissions.json), the one mainstream surface that names
    MCP tools per server ('server:tool', '*' wildcards). Entries without a
    colon are ignored by Cursor itself, so they are ignored here too.
    patterns [] with no finding means: file absent, or present with no
    mcpAllowlist key (both legitimate)."""
    path = root / ".cursor" / "permissions.json"
    try:
        if not path.is_file():
            return [], None
        raw = path.read_bytes()
    except OSError:
        return [], None
    if b"\x00" in raw[:8192]:
        return [], None
    lines = raw.decode("utf-8", errors="replace").lstrip("\ufeff") \
        .splitlines()
    try:
        doc = json.loads("\n".join(lines))
    except ValueError as exc:
        line = getattr(exc, "lineno", 1) or 1
        quote = lines[line - 1] if 0 < line <= len(lines) else ""
        return [], [_s101(
            line, quote, "permissions.json is not valid JSON",
            f"{exc} - the runtime ignores this permission file entirely")]
    allow = doc.get("mcpAllowlist") if isinstance(doc, dict) else None
    if allow is None:
        return [], None
    if not isinstance(allow, list) \
            or any(not isinstance(p, str) for p in allow):
        return [], [_s101(
            1, trim_quote(lines[0] if lines else ""),
            "mcpAllowlist is not a list of strings",
            "the tool allowlist cannot be read, so declared tool "
            "identities from this file are lost")]
    patterns = []
    for p in allow:
        srv, sep, tool = p.partition(":")
        if sep and srv.strip() and tool.strip():
            patterns.append((srv.strip(), tool.strip()))
    return patterns, None


CLAUDE_PERM_PREFIX = "mcp__"
CLAUDE_SETTINGS_RELPATHS = (".claude/settings.json",
                            ".claude/settings.local.json")


def claude_mcp_permissions(files):
    """Read Claude Code's settings-layer permission rules for MCP tools.

    files: [(display_label, Path)]. Rules look like 'mcp__<server>',
    'mcp__<server>__<tool>' (tool names may contain underscores, so the
    server is everything up to the FIRST double underscore) and live under
    permissions.allow / deny / ask.

    These are NOT capability declarations: what a permission grants is what
    the OPERATOR lets the tool do, not what the server says it does. They
    therefore land in the AUTHORITY scope artifact, verbatim, and never
    touch declared.tools or observed behavior.

    -> (entries, labeled_findings); entries are (server, level, pattern),
    labeled_findings pair each structural problem with its file label so
    callers can emit a standalone unit pointing at the right file."""
    entries = []
    labeled_findings = []
    for label, path in files:
        try:
            if not path.is_file():
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        lines = raw.decode("utf-8", errors="replace").lstrip("\ufeff") \
            .splitlines()
        try:
            doc = json.loads("\n".join(lines))
        except ValueError as exc:
            line = getattr(exc, "lineno", 1) or 1
            quote = lines[line - 1] if 0 < line <= len(lines) else ""
            labeled_findings.append((label, _s101(
                line, quote, f"{label} is not valid JSON",
                f"{exc} - the runtime ignores these permission rules "
                "entirely")))
            continue
        if not isinstance(doc, dict):
            continue
        perms = doc.get("permissions")
        if perms is None:
            continue
        if not isinstance(perms, dict):
            labeled_findings.append((label, _s101(
                1, trim_quote(lines[0] if lines else ""),
                f"{label}: permissions object cannot be read",
                "'permissions' must map allow/deny/ask to string lists; "
                "the rules are silently skipped")))
            continue
        for level in ("allow", "deny", "ask"):
            rules = perms.get(level)
            if rules is None:
                continue
            if not isinstance(rules, list) \
                    or any(not isinstance(r, str) for r in rules):
                labeled_findings.append((label, _s101(
                    1, trim_quote(lines[0] if lines else ""),
                    f"{label}: permissions.{level} cannot be read",
                    "expected a list of strings; this rule set is "
                    "silently skipped")))
                continue
            for rule in rules:
                if not rule.startswith(CLAUDE_PERM_PREFIX):
                    continue  # Bash(...), WebFetch(...), plain tools, ...
                rest = rule[len(CLAUDE_PERM_PREFIX):]
                server = rest.partition("__")[0].strip()
                if server:
                    entries.append((server, level, rule))
    return entries, labeled_findings


def _s101(line, quote, title, detail):
    return make_finding(
        "S101", "high", title, line, quote, detail)


def parse_mcp_config(lines):
    """-> (servers_dict_or_None, structural_findings).

    servers_dict None means 'nothing usable found' (either a structural
    finding explains why, or the file declares no servers at all, which is
    legitimate for every supported format)."""
    text = "\n".join(lines)
    try:
        doc = json.loads(text)
    except ValueError as exc:
        line = getattr(exc, "lineno", 1) or 1
        quote = lines[line - 1] if 0 < line <= len(lines) else ""
        return None, [_s101(
            line, quote, "MCP config is not valid JSON",
            f"{exc} - the runtime will silently load zero servers from "
            "this file")]
    if not isinstance(doc, dict):
        return None, [_s101(
            1, trim_quote(lines[0] if lines else ""),
            "MCP config root is not a JSON object",
            "runtimes expect an object keyed by mcpServers/servers; "
            "anything else is ignored")]
    servers = doc.get("mcpServers")
    if servers is None:
        servers = doc.get("servers")
    if servers is None:
        return None, []
    if not isinstance(servers, dict):
        return None, [_s101(
            1, trim_quote(lines[0] if lines else ""),
            "MCP server table is not a JSON object",
            "'mcpServers'/'servers' must map server names to entry objects")]
    return servers, []


def _anchor_line(lines, server_name):
    needle = f'"{server_name}"'
    for lineno, ln in enumerate(lines, start=1):
        if needle in ln:
            return lineno
    return 1


def build_mcp_adapter_result(name: str, cfg, root_kind, locator, anchor,
                             perm_patterns=None, claude_perms=None,
                             registrations=None, opaque=None):
    """ADAPTER LAYER OUTPUT for one MCP server entry (see block comment)."""
    strings = []
    cmd = cfg.get("command")
    if isinstance(cmd, str) and cmd.strip():
        strings.append(cmd.strip())
    args = cfg.get("args")
    arg_strings = [a.strip() for a in args
                   if isinstance(a, str) and a.strip()] \
        if isinstance(args, list) else []
    strings.extend(arg_strings)
    url = cfg.get("url")
    url_str = url.strip() if isinstance(url, str) else ""
    if url_str:
        strings.append(url_str)

    env = cfg.get("env")
    env_keys = sorted(
        k for k, v in env.items()
        if isinstance(k, str)) if isinstance(env, dict) else []

    evidence_all = {c: [] for c in CAPABILITY_CATEGORIES}

    def add(category, text, strong, src=None, line=None):
        evidence_all[category].append(
            {"src": src, "line": line if line is not None else anchor,
             "text": text, "strong": strong})

    for s in strings:
        for category, spats in SCRIPT_CAPABILITY_PATTERNS.items():
            strong = False
            hit = False
            for pat, weight in spats:
                if pat.search(s):
                    hit = True
                    if weight == "strong":
                        strong = True
                        break
            if hit:
                add(category, s, strong)
    if url_str and MCP_URL_RE.match(url_str):
        # a remote transport reaches the network by existing - the strongest,
        # least arguable form of egress evidence at the config layer
        add("network-egress", url_str, True)

    # stage 3: registrations extracted from in-tree implementation source
    # are observed behavior surface - categorized via the lexicon over
    # name + description, anchored at the registration line
    for reg in registrations or []:
        reg_text = f"{reg['name']}: {reg['description']}".strip(": ").strip()
        for category in _categories_for_text(reg_text.lower()):
            add(category, reg_text, True, src=reg["src"], line=reg["line"])

    observed = {}
    for category, evs in evidence_all.items():
        if evs:
            observed[category] = sorted(
                evs, key=lambda e: (e["src"] or "", e["line"]))

    # Depth stage 2: explicit tool declarations merge into the claim text so
    # the existing lexicon decides what they cover - nothing is inferred
    # from names alone, and absence changes nothing downstream.
    tools, tools_from_entry, _ = normalize_declared_tools(cfg.get("tools"))
    merged = {t["name"]: t for t in tools}
    perm_hits = [tool for srv, tool in (perm_patterns or [])
                 if srv.lower() == name.lower() or srv == "*"]
    for tool in perm_hits:
        merged.setdefault(tool, {"name": tool, "description": ""})
    tool_list = sorted(merged.values(), key=lambda t: t["name"])
    tools_declared = bool(tools_from_entry or perm_hits)
    tool_words = " ".join(
        f"{t['name']} {t['description']}".strip() for t in tool_list)
    claim = " ".join(x for x in (name, tool_words) if x)

    declared = declared_categories(claim.lower(), observed)

    transports = set()
    ttype = cfg.get("type")
    if isinstance(ttype, str) and ttype.strip():
        transports.add(ttype.strip())
    if cmd:
        transports.add("stdio")
    if url_str:
        transports.add("remote-url")
    mounts = sorted({a for a in arg_strings if MCP_MOUNT_RE.match(a)})

    # Claude Code settings-layer rules: operator-granted reach, recorded
    # VERBATIM under authority scope. They are never declarations and never
    # behavior - what the operator allows is not what the server claims.
    perms = [
        {"level": level, "pattern": pattern}
        for server, level, pattern in (claude_perms or [])
        if server.lower() == name.lower() or server == "*"
    ]
    perms.sort(key=lambda p: (p["pattern"], p["level"]))

    return {
        "identity": {
            "name": name,
            "kind": "mcp-server",
            "source_kind": root_kind,
            "source": locator,
        },
        "declared": {
            "text": trim_quote(claim),
            "categories": sorted(declared),
            "description_chars": 0,
            "tools_declared": bool(tool_list),
            "tools": [
                {"name": t["name"], "description": t["description"]}
                for t in tool_list
            ],
        },
        "observed_capabilities": [
            {
                "category": cat,
                "strength": "strong" if e["strong"] else "weak",
                "source": e["src"],
                "line": e["line"],
                "text": trim_quote(e["text"]),
            }
            for cat, evs in sorted(observed.items())
            for e in evs
        ],
        "authority_scope": {
            "signals": [],
            "bundle_scripts": [],
            "env_keys": env_keys,
            "transports": sorted(transports),
            "mounts": mounts,
            "permissions": perms,
            # stage 3: launch lines whose implementation surface cannot be
            # resolved inside the scanned tree - recorded verbatim, never
            # analyzed-by-fiat
            "opaque": sorted(set(opaque or [])),
        },
        "_observed_map": observed,
    }


def build_mcp_units(root_kind, display_root, rel, path: Path, stats,
                    perm_patterns=None, claude_perms=None):
    """All units from one MCP config file, in deterministic order."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:8192]:
        stats.skipped_binary += 1
        return []
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    lines = text.splitlines()
    # File roots (user-scope configs) pass rel="" so the config's own
    # display label is used verbatim - no doubled basename.
    parts = [p for p in (display_root, rel) if p and p != "."]
    cfg_display = "/".join(parts) if parts else "."

    servers, structural = parse_mcp_config(lines)
    if servers is None:
        return [McpUnit(root_kind, cfg_display, artifact=None,
                        structural=structural)]

    truncated = False
    names = sorted(servers)
    if len(names) > MCP_MAX_SERVERS_PER_CONFIG:
        names = names[:MCP_MAX_SERVERS_PER_CONFIG]
        truncated = True

    units = []
    for name in names:
        anchor = _anchor_line(lines, name)
        locator = f"{cfg_display}#mcpServers/{name}"
        entry = servers[name]
        if not isinstance(entry, dict):
            units.append(McpUnit(
                root_kind, locator, server_name=name, artifact=None,
                structural=[_s101(
                    anchor, trim_quote(f'"{name}"'),
                    "MCP server entry is not a JSON object",
                    "each server entry must map fields like command, args, "
                    "env or url; the runtime ignores this entry")]))
            continue
        has_cmd = isinstance(entry.get("command"), str) and entry["command"].strip()
        has_url = isinstance(entry.get("url"), str) and entry["url"].strip()
        if not has_cmd and not has_url:
            units.append(McpUnit(
                root_kind, locator, server_name=name, artifact=None,
                structural=[_s101(
                    anchor, trim_quote(f'"{name}"'),
                    "MCP server entry has no command or url",
                    "an entry a runtime cannot launch makes no contract "
                    "claim and serves nobody; fix or remove it")]))
            continue
        if not normalize_declared_tools(entry.get("tools"))[2]:
            units.append(McpUnit(
                root_kind, locator, server_name=name, artifact=None,
                structural=[make_finding(
                    "S102", "high", "declared tool list cannot be read",
                    anchor, trim_quote(f'"{name}"'),
                    "the entry carries a tools declaration but its shape is "
                    "unusable (expected a list of tool names or "
                    "{name, description} objects); the runtime will ignore "
                    "it and no tool contract can be verified against it")]))
            continue

        # stage 3 availability gate: launch-line arguments that resolve to
        # code files INSIDE the tree (relative to the config file) get their
        # registrations extracted; entries with none resolvable are recorded
        # verbatim under authority_scope.opaque - never fetched, never
        # guessed at
        registrations = []
        opaque = []
        raw_args = [a for a in entry.get("args") or []
                    if isinstance(a, str) and a.strip()]
        if has_cmd:
            base = path.parent
            for a in raw_args:
                if a.strip().startswith("-"):
                    continue
                candidate = base / a
                try:
                    if (candidate.suffix.lower() in CODE_SOURCE_EXTENSIONS
                            and candidate.is_file()
                            and not candidate.is_symlink()):
                        regs = extract_registrations(candidate)
                        for reg in regs:
                            reg["src"] = a.replace("\\", "/")
                        registrations.extend(regs)
                        if len(registrations) >= MAX_REGISTRATIONS_PER_FILE:
                            break
                except OSError:
                    continue
            if not registrations:
                spec = " ".join([entry["command"].strip()]
                                + [a.strip() for a in raw_args])
                opaque.append(trim_quote(spec))
        if len(registrations) > MAX_REGISTRATIONS_PER_FILE:
            registrations = registrations[:MAX_REGISTRATIONS_PER_FILE]

        artifact = build_mcp_adapter_result(
            name, entry, root_kind, locator, anchor,
            perm_patterns=perm_patterns, claude_perms=claude_perms,
            registrations=registrations, opaque=opaque)
        units.append(McpUnit(root_kind, locator, server_name=name,
                             artifact=artifact))
    if truncated:
        stats.notes.append(
            f"MCP config exceeds {MCP_MAX_SERVERS_PER_CONFIG} servers: {cfg_display}")
        stats.truncated = True
    return units


def load_baseline(path: Path):
    """-> (entries dict, error string or None)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read baseline {path}: {exc.strerror or exc}"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, f"baseline {path} is not valid JSON: {exc}"
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return None, f"baseline {path} has no 'entries' object"
    return entries, None


def write_baseline(path: Path, results, stale_fps=None):
    """Persist every current finding as the new baseline. Deterministic
    bytes for an identical tree; previously-acknowledged-but-now-absent
    fingerprints are pruned automatically."""
    entries = {}
    for res in results:
        for f in res["findings"]:
            fp = f["fingerprint"]
            if fp in entries:
                continue
            entries[fp] = {
                "rule": f["rule"],
                "severity": f["severity"],
                "title": f["title"],
                "path": res["unit"].display_path,
                "line": f["line"],
                "quote": f["quote"],
            }
    doc = {
        "tool": TOOL,
        "version": VERSION,
        "count": len(entries),
        "pruned_stale": sorted(stale_fps or []),
        "entries": entries,
    }
    # newline="\n": baselines are diffed across machines - LF always
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, indent=2,
                            sort_keys=True) + "\n")
    return len(entries)


def compute_verdict(structural, contract_findings):
    """purpose-mismatch requires a P101 at medium/high: a weak-only script
    signal (severity low) is recorded, but it does not condemn the contract
    by itself."""
    rules = {f["rule"] for f in structural + contract_findings}
    for verdict, trigger in VERDICT_PRECEDENCE:
        if verdict != "purpose-mismatch":
            if trigger in rules:
                return verdict
            continue
        mismatch = any(
            f["rule"] == "P101" and f["severity"] in ("high", "medium")
            for f in contract_findings)
        if mismatch:
            return verdict
    if rules:
        return "structural-issues"
    return "matches-declaration"


# =============================================================================
# Sources
# =============================================================================

def _home_abbreviated(p: Path) -> str:
    """Display path with $HOME folded to '~', '/'-separated."""
    try:
        s = str(p)
        home = str(Path.home())
        if s == home:
            return "~/"
        if s.startswith(home + os.sep):
            return "~/" + s[len(home) + 1:].replace(os.sep, "/")
        return s.replace(os.sep, "/")
    except Exception:
        return str(p)


def system_sources():
    """Installed skill locations, mirroring renderInstallScript()'s layout."""
    home = Path.home()
    pairs = [
        ("CLAUDE_SKILLS_DIR", home / ".claude" / "skills"),
        ("LEOSIS_SKILLS_DIR", home / ".leosis" / "skills"),
        ("HERMES_SKILLS_DIR", home / ".hermes" / "skills"),
        ("AI_SKILL_HOME", home / ".ai-skills"),
    ]
    out = []
    for env_name, default in pairs:
        raw = os.environ.get(env_name)
        target = Path(raw if raw else str(default)).expanduser()
        out.append((env_name if raw else _home_abbreviated(default), target))
    return out


def mcp_system_sources():
    """Well-known USER-SCOPE MCP config files, deterministic order.
    -> [(display_label, Path)]. Claude Desktop has no fixed cross-platform
    location, so every conventional one is listed; absent paths are skipped
    cheaply by the scan loop. Per-project server tables inside
    ~/.claude.json ('projects.<path>.mcpServers') are deliberately out of
    scope here - they belong to the scanned project tree, not to the
    machine."""
    home = Path.home()
    out = [
        ("~/.claude.json", home / ".claude.json"),
        ("~/.cursor/mcp.json", home / ".cursor" / "mcp.json"),
        ("~/.codeium/windsurf/mcp_config.json",
         home / ".codeium" / "windsurf" / "mcp_config.json"),
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(("$APPDATA/Claude/claude_desktop_config.json",
                    Path(appdata) / "Claude" / "claude_desktop_config.json"))
    out.append((
        "~/Library/Application Support/Claude/claude_desktop_config.json",
        home / "Library" / "Application Support" / "Claude"
        / "claude_desktop_config.json"))
    out.append(("~/.config/Claude/claude_desktop_config.json",
                home / ".config" / "Claude"
                / "claude_desktop_config.json"))
    return out


PROJECT_DEFAULT_ROOTS = [".claude/skills", "skills", ".cursor/rules"]


def split_typed(raw: str):
    """Parse 'KIND:PATH'. Only a recognized kind prefix is treated as the
    type; anything else is a plain local path. Keeps Windows paths
    ('C:\\...') intact - 'C' is not a known kind."""
    kind, sep, rest = raw.partition(":")
    if sep and kind in ("local", "system", "repo"):
        return kind, rest
    return "local", raw


def gather_roots(args):
    """-> [(kind, display_label, Path)] in deterministic order."""
    roots = []
    for p in args.path:
        kind, target = split_typed(p)
        roots.append((kind, target, Path(target).expanduser()))
    for raw in args.targets:
        roots.append(("local", raw, Path(raw).expanduser()))
    if args.system:
        for display, target in system_sources():
            roots.append(("system", display, target))
        # user-scope MCP configs are first-class system sources: each is a
        # FILE root that the MCP layer parses directly
        for display, target in mcp_system_sources():
            roots.append(("system", display, target))
    if not roots:
        for raw in PROJECT_DEFAULT_ROOTS:
            roots.append(("local", raw, Path(raw)))
    return roots


# =============================================================================
# Reporting
# =============================================================================

COLORS = {
    "high": "\033[0;31m",
    "medium": "\033[0;33m",
    "low": "\033[0;36m",
    "info": "\033[2m",
    "ok": "\033[0;32m",
}
RST = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ACC = "\033[1;96m"          # accent (bright cyan): the brand mark


def _shorten_locator(display_path, roots_stats):
    """Human-view locator: strip the longest matching scan-root prefix so a
    repeated absolute path does not bury the interesting part. JSON output
    always carries full locators - this view-only shortening never feeds
    fingerprints, baselines or the JSON report."""
    best = ""
    for st in roots_stats:
        gp = st.given_path
        if gp and gp != "." and display_path.startswith(gp + "/") \
                and len(gp) > len(best):
            best = gp
    return display_path[len(best) + 1:] if best else display_path


def human_report(roots_stats, results, summary_lines):
    # Color and unicode glyphs only on a real terminal; NO_COLOR
    # (https://no-color.org) always wins. Piped or CI output stays plain,
    # grep-friendly ASCII - same tree, same stream, byte-identical report.
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def paint(text, code):
        return f"{code}{text}{RST}" if use_color else text

    out = []
    if use_color:
        out.append(
            f"  {ACC}◆{RST}{BOLD} usb secscan{RST} {DIM}v{VERSION} - "
            f"skill contract verifier (static, nothing is executed){RST}")
        out.append("")
        out.append(paint("  " + "─" * 70, DIM))
        out.append(paint("  sources", DIM))
    else:
        out.append(f"{TOOL} v{VERSION} - skill contract verifier "
                   "(static, nothing is executed)")
        out.append("")
        out.append("sources:")
    if not roots_stats:
        out.append("  (none found)")
    for st in roots_stats:
        mark = "" if st.exists else "  [missing]"
        out.append(f"  {st.kind:<7} {st.given_path}{mark}")
    out.append("")
    for res in results:
        unit, entry = res["unit"], res
        verdict = entry["verdict"]
        loc = _shorten_locator(unit.display_path, roots_stats)
        if entry["findings"]:
            glyph = paint("✗ ", COLORS["high"]) if use_color else ""
        else:
            glyph = paint("✓ ", COLORS["ok"]) if use_color else ""
        vtag = paint(verdict.upper(),
                     COLORS["medium"] if verdict == "purpose-mismatch"
                     else DIM)
        out.append(f"  {glyph}{paint(loc, BOLD)}  "
                   f"[{paint(unit.format, DIM)}]  contract: {vtag}")
        if not entry["findings"]:
            if use_color:
                out.append(paint(
                    "    observed behavior is covered by the declared "
                    "contract", COLORS["ok"]))
            else:
                out.append(
                    "    ok - observed behavior is covered by the declared "
                    "contract")
        for f in entry["findings"]:
            sev = f["severity"].upper()
            corr = " (corroborated)" if f.get("corroborated") else ""
            adv = paint("  [advisory]", DIM) if f["rule"] == "P103" else ""
            base = ""
            if f.get("baseline") == "acknowledged":
                base = paint("  [baselined]", DIM)
            out.append("    " + paint(
                f"{sev:<6} {f['rule']} {f['title']} (line {f['line']}){corr}",
                COLORS[f["severity"]] if f.get("baseline") != "acknowledged" else DIM
            ) + adv + base)
            if f.get("evidence_path"):
                out.append(f"            {f['evidence_path']}:{f['line']}: "
                           f"{f['quote']}")
            elif f["quote"]:
                out.append(f"            {f['quote']}")
            out.append(paint(f"            why: {f['detail']}", DIM))
        for s in entry["authority_signals"]:
            out.append(paint(
                f"    note  {s['rule']} {s['title']} (line {s['line']}) - "
                f"{trim_quote(s['matched'])}", DIM))
        out.append("")
    if summary_lines:
        first = summary_lines[0]
        if first.startswith("summary:") and use_color:
            out.append(paint("summary:", DIM) + first[len("summary:"):])
        else:
            out.append(first)
        out.extend(paint(ln, DIM) for ln in summary_lines[1:])
    return "\n".join(out)


def canonical_finding(f):
    """Finding fields that carry contract meaning (explanatory prose and
    run-scoped policy state are deliberately excluded)."""
    out = {
        "rule": f["rule"],
        "severity": f["severity"],
        "title": f["title"],
        "line": f["line"],
        "quote": f["quote"],
        "fingerprint": f["fingerprint"],
    }
    if f.get("evidence_path"):
        out["evidence_path"] = f["evidence_path"]
    if f.get("corroborated"):
        out["corroborated"] = True
    return out


def emit_contracts(path: Path, results):
    """Write the four contract artifacts per unit as canonical JSON.

    This is the Migration Step 1 emission: a stable, deterministic snapshot
    of what the engine understood about each unit, independent of report
    formatting. Schema: usb-contract-artifacts/v1 (see docs/contract-core.md).
    """
    units_out = []
    for res in results:
        art = dict(res["artifacts"]) if res["artifacts"] else {
            # Unverifiable units (malformed frontmatter) never reach the
            # adapter; their identity/declaration are still reported, exactly
            # as the pre-refactor emitter did.
            "identity": {
                "name": res["unit"].name,
                "kind": res["unit"].format,
                "source_kind": res["unit"].root_kind,
                "source": res["unit"].display_path,
            },
            "declared": {
                "text": trim_quote(res["unit"].claimed_purpose_text()),
                "categories": [],
                "description_chars": len(res["unit"].description),
            },
            "observed_capabilities": [],
            "authority_scope": {"signals": [], "bundle_scripts": []},
            "undeclared_categories": [],
        }
        # process-local inputs never leave the artifact boundary
        art.pop("_context", None)
        art.pop("_observed_map", None)
        art.pop("_signals", None)

        evidence_by_rule = {}
        for f in res["findings"]:
            entry = {"line": f["line"], "quote": f["quote"]}
            if f.get("evidence_path"):
                entry["path"] = f["evidence_path"]
            evidence_by_rule.setdefault(f["rule"], []).append(entry)
        art["evidence_by_rule"] = dict(sorted(evidence_by_rule.items()))

        art["structural_findings"] = [canonical_finding(f)
                                      for f in res["findings"]
                                      if f["rule"].startswith("S")]
        art["contract_findings"] = [canonical_finding(f)
                                    for f in res["findings"]
                                    if not f["rule"].startswith("S")]
        art["verdict"] = res["verdict"]

        units_out.append({
            "identity": art["identity"],
            "declared": art["declared"],
            "observed_capabilities": art["observed_capabilities"],
            "evidence_by_rule": art["evidence_by_rule"],
            "authority_scope": art["authority_scope"],
            "undeclared_categories": art["undeclared_categories"],
            "structural_findings": art["structural_findings"],
            "contract_findings": art["contract_findings"],
            "verdict": art["verdict"],
        })
    doc = {
        "tool": TOOL,
        "version": VERSION,
        "schema": "usb-contract-artifacts/v1",
        "unit_count": len(units_out),
        "units": units_out,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": byte-determinism across platforms (a Windows write_text
    # would emit CRLF and drift from the LF golden on every Linux CI run)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, indent=2,
                            sort_keys=True) + "\n")


def main(argv=None):
    # Pin output to UTF-8 regardless of platform/locale: findings quote skill
    # text that may contain any Unicode, and a cp1254/cp1252 stdout would
    # either crash or mangle it - both breaking the determinism contract.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="usb secscan",
        description="Skill Contract Verifier - static security scan for agent skills.",
        epilog="exit codes: 0 clean/low/info, 1 medium/high findings, 2 error",
    )
    parser.add_argument("targets", nargs="*", help="local paths to scan")
    parser.add_argument("--path", action="append", default=[],
                        help="typed source KIND:PATH (local|system|repo); repeatable")
    parser.add_argument("--system", action="store_true",
                        help="scan installed system skill locations")
    parser.add_argument("--json", action="store_true", help="JSON report on stdout")
    parser.add_argument("--baseline", metavar="FILE",
                        help="acknowledge findings recorded in FILE; only new "
                             "findings gate the exit code")
    parser.add_argument("--write-baseline", nargs="?", const=BASELINE_DEFAULT_PATH,
                        default=None, metavar="FILE",
                        help="record every current finding as the new baseline "
                             f"(default: {BASELINE_DEFAULT_PATH}) and prune stale entries")
    parser.add_argument("--emit-contracts", metavar="FILE",
                        help="write the four contract artifacts (declared / "
                             "observed / evidence / authority) as canonical "
                             "JSON; additive debug output, stdout unchanged")
    parser.add_argument("--version", action="version", version=f"{TOOL} {VERSION}")
    args = parser.parse_args(argv)

    roots = gather_roots(args)
    if not roots:
        parser.error("nothing to scan: pass paths, --system, or run inside a project")

    baseline_entries = None
    if args.baseline:
        bpath = Path(args.baseline).expanduser()
        baseline_entries, err = load_baseline(bpath)
        if err:
            print(f"{TOOL}: error: {err}", file=sys.stderr)
            return EXIT_ERROR
    write_path = None
    if args.write_baseline:
        write_path = Path(args.write_baseline).expanduser()

    # Explicit targets must exist (typo = operational error, exit 2);
    # implicit/system roots may be absent - reported as missing instead.
    for raw in args.targets + [split_typed(p)[1] for p in args.path]:
        target = Path(raw).expanduser()
        if not target.exists():
            print(f"{TOOL}: error: path does not exist: {raw}", file=sys.stderr)
            return EXIT_ERROR
        if not target.is_file() and not target.is_dir():
            print(f"{TOOL}: error: not a file or directory: {raw}", file=sys.stderr)
            return EXIT_ERROR

    roots_stats = []
    units = []
    consumed_realpaths = set()

    # user-scope Claude Code permission rules travel with the machine and
    # apply to every system-scope MCP unit; loaded once so their findings
    # are reported exactly once
    sys_claude_entries, sys_claude_findings = [], []
    if args.system:
        sys_claude_entries, sys_claude_findings = claude_mcp_permissions([
            ("~/.claude/settings.json",
             Path.home() / ".claude" / "settings.json"),
        ])

    for kind, display, path in roots:
        exists = path.exists()
        stats = RootStats(kind, display, exists)
        roots_stats.append(stats)
        if not exists:
            continue
        candidates = [(path.name, path)] if path.is_file() \
            else walk_candidates(path, stats)

        # MCP configs under this root are parsed by the MCP layer below;
        # the markdown pipeline must never see them as skill files.
        mcp_here = find_mcp_configs(path)
        mcp_rps = {os.path.realpath(p) for _, p in mcp_here}
        # depth stage 2: the permission layer may name tools per server;
        # Claude Code settings rules are operator authority, project-scoped
        # for directory roots and user-scoped for system config files
        root_is_file = path.is_file()
        perm_patterns, perm_bad = ([], None) if root_is_file \
            else cursor_mcp_allowlist(path)
        claude_files = []
        if not root_is_file:
            claude_files = [
                (f"{display}/{relp}" if display != "." else relp,
                 path / relp)
                for relp in CLAUDE_SETTINGS_RELPATHS
            ]
        claude_entries, claude_findings = claude_mcp_permissions(claude_files) \
            if not root_is_file else (sys_claude_entries if kind == "system"
                                      else [], [])

        for rel, abspath in candidates:
            if len(units) >= MAX_SKILLS:
                stats.truncated = True
                break
            rp = os.path.realpath(abspath)
            if rp in consumed_realpaths or rp in mcp_rps:
                continue
            built = build_unit(kind, display, rel, abspath)
            if built == "binary" or built is None:
                stats.skipped_binary += 1
                continue
            rel_posix = rel.replace("\\", "/")
            root_name = path.name if path.is_dir() else ""
            loc_card = is_card_by_location(rel_posix, root_name) or path.is_file()
            fm_keys = built.fm["meta"]
            qualifies = (
                built.format == "claude"
                or loc_card
                or ("name" in fm_keys and "description" in fm_keys)
            )
            if not qualifies:
                continue  # documentation/reference file, not a capability claim
            if built.format == "card":
                built.format = definition_format(rel_posix)
            consumed_realpaths.add(rp)
            units.append(built)

        # MCP config layer (contract-core depth-order stage 1): well-known
        # JSON config files under this root each contribute server units.
        for cfg_rel, cfg_path in mcp_here:
            if len(units) >= MAX_SKILLS:
                stats.truncated = True
                break
            rp = os.path.realpath(cfg_path)
            if rp in consumed_realpaths:
                continue
            consumed_realpaths.add(rp)
            stats.candidates += 1
            units.extend(build_mcp_units(
                kind, display, "" if root_is_file else cfg_rel,
                cfg_path, stats, perm_patterns=perm_patterns,
                claude_perms=claude_entries))
        # a permission file no runtime can read is itself worth reporting:
        # its allowlist silently stops applying
        if perm_bad is not None:
            plabel = ".cursor/permissions.json" if display == "." \
                else f"{display}/.cursor/permissions.json"
            units.append(McpUnit(kind, plabel, artifact=None,
                                 structural=[perm_bad]))
        for clabel, cfinding in claude_findings:
            units.append(McpUnit(kind, clabel, artifact=None,
                                 structural=[cfinding]))

    # user-scope settings problems: exactly once, regardless of how many
    # system-scope config files were scanned
    for clabel, cfinding in sys_claude_findings:
        units.append(McpUnit("system", clabel, artifact=None,
                             structural=[cfinding]))

    units.sort(key=lambda u: (KIND_RANK[u.root_kind], u.display_path))

    seen_names = {}
    results = []
    authority_signal_total = 0
    skills_with_signals = 0
    verdict_counts = {}
    counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
    by_rule = {}

    for unit in units:
        signals = []
        contract_findings = []
        observed = {}
        declared = set()
        artifacts = None
        if unit.format == "mcp-server":
            # MCP adapter: the artifact is prebuilt at discovery; core rules
            # run on it exactly as they do for skills. P102 has no
            # description to check, and with zero authority signals nothing
            # can corroborate - so P101 alone decides.
            structural = list(unit._structural)
            if unit._artifact is not None:
                artifacts = unit._artifact
                observed = artifacts["_observed_map"]
                declared = set(artifacts["declared"]["categories"])
                artifacts["undeclared_categories"] = sorted(
                    set(observed) - declared)
                contract_findings = p101_findings(artifacts)
                contract_findings.extend(p103_finding(artifacts))
        else:
            structural, fields_ok = analyze_structure(unit, seen_names)
            if fields_ok:
                # ADAPTER LAYER: everything below consumes the artifact only.
                artifacts = build_adapter_result(unit)
                # Rich signals (with explanatory detail) stay the public face;
                # the serialized copies inside the artifact are for emission.
                signals = artifacts["_signals"]
                observed = artifacts["_observed_map"]
                declared = set(artifacts["declared"]["categories"])
                artifacts["undeclared_categories"] = sorted(
                    set(observed) - declared)
                contract_findings = p101_findings(artifacts)
                contract_findings.extend(p102_finding(artifacts))
                if any(f["rule"] == "P101" for f in contract_findings):
                    contract_findings.extend(
                        corroborated_signal_findings(signals))

        findings = cap_per_rule(structural + contract_findings)
        if baseline_entries is not None:
            for f in findings:
                f["baseline"] = ("acknowledged"
                                 if f["fingerprint"] in baseline_entries
                                 else "new")
        verdict = compute_verdict(structural, contract_findings)

        flagged = bool(findings)
        if signals:
            authority_signal_total += len(signals)
            skills_with_signals += 1
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        for f in findings:
            counts[f["severity"]] += 1
            by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1

        results.append({
            "unit": unit,
            "artifacts": artifacts,
            "findings": findings,
            "authority_signals": signals,
            "verdict": verdict,
            "observed": observed,
            "declared": declared,
            "flagged": flagged,
        })

    flagged_count = sum(1 for r in results if r["flagged"])

    # Exit gating: without a baseline, any medium/high finding blocks. With
    # one, only findings NOT recorded in the baseline block.
    if baseline_entries is None:
        blocking = counts["high"] + counts["medium"]
        new_blocking = blocking
        acknowledged = 0
        stale_fps = []
    else:
        new_counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
        acknowledged = 0
        current_fps = set()
        for res in results:
            for f in res["findings"]:
                current_fps.add(f["fingerprint"])
                if f.get("baseline") == "acknowledged":
                    acknowledged += 1
                else:
                    new_counts[f["severity"]] += 1
        stale_fps = sorted(set(baseline_entries) - current_fps)
        new_blocking = new_counts["high"] + new_counts["medium"]
        blocking = new_blocking

    verdict_order = ["matches-declaration", "excessive-authority",
                     "structural-issues", "purpose-mismatch",
                     "unverifiable"]
    vparts = []
    for k in (verdict_order + sorted(set(verdict_counts)
                                     - set(verdict_order))):
        if k in verdict_counts:
            adv = " (advisory)" if k == "excessive-authority" else ""
            vparts.append(f"{k}:{verdict_counts[k]}{adv}")
    exit_note = ("exit 1 - blocking findings present" if blocking
                 else "exit 0 - nothing blocking")
    summary_lines = [
        f"summary: {len(units)} skill(s) verified, {flagged_count} with "
        f"findings | {exit_note}",
        "  verdicts: " + " · ".join(vparts),
        f"  findings: high:{counts['high']} medium:{counts['medium']} "
        f"low:{counts['low']} info:{counts['info']}",
        f"  authority signals: {authority_signal_total} across "
        f"{skills_with_signals} skill(s) (always advisory)",
    ]
    if baseline_entries is not None:
        summary_lines.append(
            f"  baseline: {acknowledged} acknowledged, {new_blocking} new "
            f"blocking, {len(stale_fps)} stale")

    if write_path is not None:
        written = write_baseline(write_path, results, stale_fps)
        summary_lines.append(
            f"  baseline written: {write_path} ({written} entries)")

    if args.emit_contracts:
        emit_contracts(Path(args.emit_contracts).expanduser(), results)

    if args.json:
        report = {
            "tool": TOOL,
            "version": VERSION,
            "model": "skill-contract-verifier",
            "roots": [st.to_json() for st in roots_stats],
            "skills": [
                {
                    "source": {"kind": r["unit"].root_kind,
                               "path": r["unit"].display_path},
                    "format": r["unit"].format,
                    "name": r["unit"].name,
                    "contract": {
                        "declared_text": (
                            r["artifacts"]["declared"]["text"]
                            if r.get("artifacts")
                            else ""),
                        "observed_categories": sorted(r["observed"]),
                        "declared_categories": sorted(r["declared"]),
                        "undeclared_categories": sorted(
                            set(r["observed"]) - set(r["declared"])),
                    },
                    "verdict": r["verdict"],
                    "findings": r["findings"],
                    "authority_signals": r["authority_signals"],
                }
                for r in results
            ],
            "summary": {
                "skills": len(units),
                "flagged": flagged_count,
                "counts": counts,
                "by_rule": dict(sorted(by_rule.items())),
                "verdicts": dict(sorted(verdict_counts.items())),
                "authority_signals": authority_signal_total,
                "skills_with_authority_signals": skills_with_signals,
                "exit_code": EXIT_FINDINGS if blocking else EXIT_CLEAN,
            },
        }
        if baseline_entries is not None:
            report["baseline"] = {
                "file": args.baseline,
                "acknowledged": acknowledged,
                "new_blocking": new_blocking,
                "stale_entries": stale_fps,
            }
        if write_path is not None:
            report["baseline_written"] = str(write_path)
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2,
                                    sort_keys=True) + "\n")
    else:
        sys.stdout.write(human_report(roots_stats, results,
                                      summary_lines) + "\n")

    return EXIT_FINDINGS if blocking else EXIT_CLEAN


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(EXIT_ERROR)
