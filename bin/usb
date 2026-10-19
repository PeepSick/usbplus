#!/usr/bin/env bash
# usb — Universal Systems Bridge CLI
# https://github.com/PeepSick/usbplus
#
# Install:
#   curl -fsSL https://usbplus.peepsicklabs.com/usb -o /usr/local/bin/usb && chmod +x /usr/local/bin/usb
#
# Usage:
#   usb                            Open the interactive picker (no subcommand = menu)
#   usb install <slug>             Install a single skill
#   usb install <domain>           Install one domain (8 workflows)
#   usb install <preset>           Install a curated preset (web-dev, backend, security, ...)
#   usb install                    Install the whole pack (529 skills)
#   usb install --target=claude    Pick a runtime explicitly (space form works too)
#   usb install --dry-run          Show the install script without executing
#   usb uninstall <slug>           Remove an installed skill from a runtime (offline)
#   usb uninstall --dry-run        Show what would be removed, remove nothing
#   usb search <query>             Search the catalog
#   usb info <slug>                Show details about one skill
#   usb list                       List all skills
#   usb secscan [paths...]         Security-scan skills (project-local dirs by default)
#   usb secscan --system           Security-scan installed system skill locations
#   usb secscan --repo <src>       Security-scan skills in a git/GitHub repo (read-only)
#                                  ("usb scan" is a shorthand alias)
#   usb mcprobe [paths...]         Runtime observation of MCP servers (LAUNCHES
#                                  targets - opt-in via --yes; non-deterministic)
#   usb mcp                        Start the MCP stdio server (for AI agents)
#   usb version                    Show USB pack version
#   usb --help                     Show this help
#
# Environment:
#   USB_BASE_URL    Override the catalog host (default https://usbplus.peepsicklabs.com)
#   USB_TARGET      Default runtime target (default: auto)

set -euo pipefail

USB_BASE_URL="${USB_BASE_URL:-https://usbplus.peepsicklabs.com}"
USB_TARGET="${USB_TARGET:-auto}"

# --- pretty output helpers ---------------------------------------------------
# One visual language for every subcommand. Color and unicode glyphs turn on
# only on a real terminal and only when NO_COLOR is unset - piped or CI
# output stays plain, grep-friendly ASCII. Nothing here affects exit codes.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  RED=$'\033[0;31m'
  GRN=$'\033[0;32m'
  YEL=$'\033[0;33m'
  CYA=$'\033[0;36m'
  ACC=$'\033[1;96m'      # accent (bright cyan): the brand mark
  DIM=$'\033[2m'
  BOLD=$'\033[1m'
  RST=$'\033[0m'
  G_INFO="●"
  G_OK="✓"
  G_WARN="▲"
  G_ERR="✗"
  G_MARK="◆"
else
  RED=""; GRN=""; YEL=""; CYA=""; ACC=""; DIM=""; BOLD=""; RST=""
  G_INFO=">"
  G_OK="ok:"
  G_WARN="warning:"
  G_ERR="error:"
  G_MARK=""
fi

info() { printf '%s%s%s %s\n' "$CYA" "$G_INFO" "$RST" "$*"; }
ok()   { printf '%s%s%s %s\n' "$GRN" "$G_OK" "$RST" "$*"; }
warn() { printf '%s%s%s %s\n' "$YEL" "$G_WARN" "$RST" "$*" >&2; }
err()  { printf '%s%s%s %s\n' "$RED" "$G_ERR" "$RST" "$*" >&2; }

# dim horizontal rule across an 80-column frame
rule() { printf '%s%s%s\n' "$DIM" "  ──────────────────────────────────────────────────────────────────────" "$RST"; }

# brand tag: "◆ usb" in the accent color (empty glyph in plain mode)
brand() { printf '%s%s%s%susb%s' "$ACC" "$G_MARK" "$RST" "$BOLD" "$RST"; }

usage() {
  # Print only the leading '#' comment block (skipping the shebang), stopping
  # at the first non-comment line. The previous implementation sliced a fixed
  # line range (`sed -n '2,30p'`), so once the header grew past line 30,
  # --help started leaking live script code: `set -euo pipefail` and the
  # USB_BASE_URL/USB_TARGET assignments appeared verbatim in the help text.
  # Deriving the range from the comment block itself keeps help and header in
  # sync no matter how many usage lines are added.
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

# --- ensure curl + bash present ---------------------------------------------
for bin in curl bash; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    err "Missing required tool: $bin"
    exit 1
  fi
done

# --- version subcommand ------------------------------------------------------
cmd_version() {
  local v
  v=$(curl -fsSL "$USB_BASE_URL/api/version" 2>/dev/null || true)
  if [ -z "$v" ]; then
    err "Could not reach $USB_BASE_URL"
    exit 1
  fi
  printf '%s\n' "$v"
}

# --- search subcommand -------------------------------------------------------
cmd_search() {
  local query="${1:-}"
  if [ -z "$query" ]; then
    err "search needs a query"
    exit 1
  fi
  local url="$USB_BASE_URL/api/skills?target=generic"
  info "Searching $USB_BASE_URL for '$query' ..."

  if command -v python3 >/dev/null 2>&1; then
    # Script goes to a temp file (not stdin) so it never competes with the
    # curl | python3 pipe below for python3's stdin — a heredoc on the same
    # command as an input pipe silently wins over the pipe, which used to
    # starve json.load(sys.stdin) of any data.
    local script
    script=$(mktemp)
    trap 'rm -f "$script"' RETURN
    cat > "$script" <<'PY'
import json, sys
query = sys.argv[1].lower() if len(sys.argv) > 1 else ""
data = json.load(sys.stdin)
hits = [s for s in data["bundle"]["skills"]
        if query in s["slug"].lower() or query in s["name"].lower()
        or query in s.get("description", "").lower()
        or query in s.get("category", "").lower()]
for s in hits[:30]:
    print(f"  {s['slug']:42s}  {s.get('category',''):20s}  {s.get('name','')}")
print(f"\n{len(hits)} match(es).")
PY
    curl -fsSL "$url" | python3 "$script" "$query"
  else
    warn "python3 not available — falling back to grep on full manifest"
    curl -fsSL "$url" | grep -iE "$query" | head -30 || true
  fi
}

# --- info subcommand ---------------------------------------------------------
cmd_info() {
  local slug="${1:-}"
  if [ -z "$slug" ]; then
    err "info needs a skill slug (try: usb info intent-router)"
    exit 1
  fi
  curl -fsSL "$USB_BASE_URL/api/audit/$slug" | python3 -m json.tool || {
    err "Skill not found: $slug"
    exit 1
  }
}

# --- list subcommand ---------------------------------------------------------
cmd_list() {
  local url="$USB_BASE_URL/api/skills?target=generic"
  # Bind to locals before formatting: this script is single-quoted for bash,
  # so a nested \" reaches Python literally and is a syntax error inside an
  # f-string expression — which silently dropped this command into the raw
  # grep fallback below.
  curl -fsSL "$url" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for s in d["bundle"]["skills"]:
    slug = s["slug"]
    category = s.get("category", "")
    name = s.get("name", "")
    print(f"  {slug:42s}  {category:20s}  {name}")
' || {
    warn "python3 not available"
    curl -fsSL "$url" | grep -oE '"slug": "[^"]+"' | head -100
  }
}

# --- secscan subcommand ------------------------------------------------------
# Static security scan of agent skills. Three source kinds:
#   local   paths you pass (default: project dirs .claude/skills, skills,
#           .cursor/rules when nothing is passed)
#   system  installed skill locations on this machine (~/.claude/skills,
#           ~/.leosis/skills, ~/.hermes/skills, $AI_SKILL_HOME or
#           ~/.ai-skills; env overrides honored)
#   repo    a git/GitHub URL fetched read-only into a temp dir and scanned
#           there; nothing from the repository is ever executed
#
# The analysis itself lives in usb-secscan.py next to this script (python3,
# stdlib only). Unlike search/list there is NO grep fallback: a security
# verdict degraded to grep would be worse than no verdict.
#
# Contract (documented in README): deterministic — an identical input tree
# yields a byte-identical report; exit 0 = clean/low/info only, 1 = medium or
# high findings, 2 = operational error (bad path, unreachable repo).

USB_SECSCAN_ENGINE="$(cd "$(dirname "$0")" && pwd)/usb-secscan.py"
USB_SECSCAN_WORKDIR=""

# Hand paths to the engine in native form. Under Git Bash/MSYS the engine
# runs as a native Windows python3, and MSYS's automatic argument conversion
# is unreliable for combined 'kind:/mpx/path' strings and for /tmp (a virtual
# mount): without an explicit conversion Windows python cannot see them.
# cygpath -m emits forward-slash drive paths (C:/...) that every OS accepts;
# on Linux/macOS cygpath does not exist and paths pass through unchanged.
secscan_to_native_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m -- "$1"
  else
    printf '%s\n' "$1"
  fi
}

secscan_cleanup() {
  if [ -n "$USB_SECSCAN_WORKDIR" ] && [ -d "$USB_SECSCAN_WORKDIR" ]; then
    rm -rf "$USB_SECSCAN_WORKDIR"
  fi
}

secscan_fetch_repo() {
  # $1 = git URL | github "owner/repo" shorthand | existing local dir
  # $2 = destination directory inside the temp workdir
  # Prints the path that should be scanned on success.
  local spec="$1" dest="$2"
  local clone_url="" gh_repo="" gh_ref="" gh_sub=""

  if [ -d "$spec" ]; then
    printf '%s\n' "$spec"
    return 0
  fi

  case "$spec" in
    file://*)
      # Local filesystem URL: scan in place, no clone. (Used by tests and
      # for scanning a working tree without copying it.)
      local local_path="${spec#file://}"
      local_path="${local_path#localhost}"
      if [ -d "$local_path" ]; then
        printf '%s\n' "$local_path"
        return 0
      fi
      err "secscan: file:// URL is not an existing directory: $spec"
      return 1
      ;;
    https://github.com/*|http://github.com/*)
      # github.com/o/r[/tree/<ref>[/<subdir>]] — split so the ref can be
      # cloned directly and only the requested subdirectory scanned.
      local p="${spec#*github.com/}"
      local owner repo kind3
      owner=$(printf '%s' "$p" | cut -d/ -f1)
      repo=$(printf '%s' "$p" | cut -d/ -f2 | sed 's/\.git$//')
      kind3=$(printf '%s' "$p" | cut -d/ -f3)
      if [ "$kind3" = "tree" ]; then
        gh_ref=$(printf '%s' "$p" | cut -d/ -f4)
        gh_sub=$(printf '%s' "$p" | cut -s -d/ -f5-)
      fi
      gh_repo="$owner/$repo"
      clone_url="https://github.com/${gh_repo}.git"
      ;;
    https://*|http://*|git://*|ssh://*|git@*)
      clone_url="$spec" ;;
    *)
      if [[ "$spec" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
        gh_repo="$spec"
        clone_url="https://github.com/${gh_repo}.git"
      else
        err "secscan: don't know how to fetch '$spec'"
        err "use a git URL, github 'owner/repo', or an existing local directory"
        return 1
      fi
      ;;
  esac

  mkdir -p "$dest" || return 1

  if command -v git >/dev/null 2>&1; then
    # Progress goes to stderr: this function's stdout IS its return value
    # (the scanned path) and is captured via $(...) by cmd_secscan.
    info "Fetching ${gh_repo:-$clone_url}${gh_ref:+ (ref: $gh_ref)} ..." >&2
    if [ -n "$gh_ref" ]; then
      GIT_TERMINAL_PROMPT=0 git clone --quiet --depth 1 --branch "$gh_ref" -- "$clone_url" "$dest" 2>/dev/null \
        || GIT_TERMINAL_PROMPT=0 git clone --quiet --depth 1 -- "$clone_url" "$dest" 2>/dev/null
    else
      GIT_TERMINAL_PROMPT=0 git clone --quiet --depth 1 -- "$clone_url" "$dest" 2>/dev/null
    fi
    if [ $? -ne 0 ] || [ ! -d "$dest" ]; then
      rm -rf "$dest"
      err "secscan: could not clone $clone_url${gh_ref:+ (ref: $gh_ref)}"
      return 1
    fi
  elif [ -n "$gh_repo" ]; then
    warn "git not found — falling back to a GitHub tarball download"
    curl -fsSL "https://codeload.github.com/${gh_repo}/tar.gz/${gh_ref:-HEAD}" \
      | tar -x -C "$dest" --strip-components=1
    if [ $? -ne 0 ] || [ ! -d "$dest" ]; then
      rm -rf "$dest"
      err "secscan: could not download the tarball for $gh_repo"
      return 1
    fi
  else
    rm -rf "$dest"
    err "secscan: git is required to fetch $spec"
    return 1
  fi

  if [ -n "$gh_sub" ]; then
    if [ ! -d "$dest/$gh_sub" ]; then
      err "secscan: repository has no '$gh_sub' directory"
      return 1
    fi
    printf '%s\n' "$dest/$gh_sub"
  else
    printf '%s\n' "$dest"
  fi
}

cmd_secscan() {
  command -v python3 >/dev/null 2>&1 || {
    err "secscan needs python3 in PATH — there is no fallback for security analysis"
    exit 2
  }
  [ -f "$USB_SECSCAN_ENGINE" ] || {
    err "secscan engine not found at $USB_SECSCAN_ENGINE"
    err "the security engines ship with the npm package — npm install -g @peepsick/usb-cli"
    exit 2
  }

  local py_args=() repos=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --json)         py_args+=("--json") ;;
      --system|--all) py_args+=("--system") ;;
      --baseline=*)   py_args+=("--baseline" "${1#--baseline=}") ;;
      --baseline)
        shift
        [ $# -gt 0 ] || { err "--baseline needs a file recorded by --write-baseline"; exit 2; }
        case "$1" in /*) py_args+=("--baseline" "$(secscan_to_native_path "$1")") ;; *) py_args+=("--baseline" "$1") ;; esac
        ;;
      --write-baseline=*) py_args+=("--write-baseline=${1#--write-baseline=}") ;;
      --write-baseline)
        # The engine defines --write-baseline with an optional value; a space
        # form must consume the next token exactly like the engine does,
        # otherwise the file name silently becomes a scan root.
        if [ $# -gt 1 ]; then
          shift
          case "$1" in /*) py_args+=("--write-baseline=$(secscan_to_native_path "$1")") ;; *) py_args+=("--write-baseline=$1") ;; esac
        else
          py_args+=("--write-baseline")
        fi
        ;;
      --emit-contracts=*) py_args+=("--emit-contracts=${1#--emit-contracts=}") ;;
      --emit-contracts)
        shift
        [ $# -gt 0 ] || { err "--emit-contracts needs a file path to write"; exit 2; }
        case "$1" in /*) py_args+=("--emit-contracts" "$(secscan_to_native_path "$1")") ;; *) py_args+=("--emit-contracts" "$1") ;; esac
        ;;
      --repo=*)       repos+=("${1#--repo=}") ;;
      --repo)
        shift
        [ $# -gt 0 ] || { err "--repo needs a URL, 'owner/repo', or a local repository path"; exit 2; }
        repos+=("$1")
        ;;
      -h|--help)      usage; exit 0 ;;
      --*)            warn "Unknown flag: $1" ;;
      *)
        # Relative paths pass through untouched; absolute POSIX-style paths
        # (/c/..., /tmp/...) must become native before python sees them.
        case "$1" in
          /*) py_args+=("--path" "local:$(secscan_to_native_path "$1")") ;;
          *)  py_args+=("--path" "local:$1") ;;
        esac
        ;;
    esac
    shift
  done

  local rc=0 i=0 r fetched
  if [ "${#repos[@]}" -gt 0 ]; then
    USB_SECSCAN_WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/usb-secscan-work.XXXXXXXX" 2>/dev/null) \
      || { err "could not create a temp directory"; exit 2; }
    trap secscan_cleanup EXIT
    for r in ${repos[@]+"${repos[@]}"}; do
      i=$((i+1))
      if fetched=$(secscan_fetch_repo "$r" "$USB_SECSCAN_WORKDIR/repo-$i"); then
        py_args+=("--path" "repo:$(secscan_to_native_path "$fetched")")
      else
        rc=2
      fi
    done
  fi

  if [ "$rc" -eq 0 ]; then
    python3 "$(secscan_to_native_path "$USB_SECSCAN_ENGINE")" ${py_args[@]+"${py_args[@]}"} || rc=$?
  fi
  secscan_cleanup
  USB_SECSCAN_WORKDIR=""
  [ "$rc" -ne 0 ] && exit "$rc"
  return 0
}

# --- install subcommand ------------------------------------------------------
cmd_install() {
  local filter_args=""
  local target="$USB_TARGET"
  local show_script=0

  # A while/shift loop rather than `for arg in "$@"`: the space form
  # (`--target claude`) has to consume the NEXT token, which a for-loop
  # cannot skip — the value would fall through to the filter branch and turn
  # into `slug=claude`, installing a skill nobody asked for.
  while [ $# -gt 0 ]; do
    case "$1" in
      --target=*)  target="${1#--target=}" ;;
      --target)
        shift
        [ $# -gt 0 ] || { err "--target needs a value (e.g. --target claude)"; exit 2; }
        target="$1"
        ;;
      --dry-run)   show_script=1 ;;
      --help|-h)   usage; exit 0 ;;
      --*)         warn "Unknown flag: $1" ;;
      *)
        if [ -z "${filter_args:-}" ]; then
          case "$1" in
            core-only|web-dev|backend|infra|security|data)
              filter_args="preset=$1" ;;
            *)
              filter_args="slug=$1" ;;
          esac
        fi
        ;;
    esac
    shift
  done

  local url="$USB_BASE_URL/api/install?target=$target"
  if [ -n "${filter_args:-}" ]; then
    url="$url&$filter_args"
  fi

  if [ "$show_script" = "1" ]; then
    info "Dry run — installer script:"
    curl -fsSL "$url"
    return 0
  fi

  info "Installing via $url ..."
  # Download to a file rather than `curl | bash`: piping makes the installer's
  # stdin the pipe carrying its own script text, so its interactive runtime
  # picker consumes those leftover lines as answers instead of ever waiting
  # for the user. Running it as a file argument leaves stdin on the terminal.
  local script
  script=$(mktemp) || { err "Could not create a temp file"; exit 1; }
  if ! curl -fsSL "$url" -o "$script"; then
    rm -f "$script"
    err "Could not download the installer from $url"
    exit 1
  fi
  bash "$script"
  local rc=$?
  rm -f "$script"
  if [ "$rc" != 0 ]; then
    exit "$rc"
  fi
  ok "Done."
}

# --- uninstall subcommand -----------------------------------------------------
# The mirror of `usb install <slug>`: take one skill back out of a runtime
# target. Entirely local — no network, no catalog — so it works offline and
# never has to agree with a server about what is on this machine.
#
# This command deletes files, so three rules hold before any rm runs:
#   1. The slug must be catalog-shaped ([a-z0-9-],1-64 chars, not leading
#      dash) before it is used anywhere — no path separator or shell
#      metacharacter can reach a path from user input.
#   2. Only paths the target's own layout would have written are considered
#      (the same `case "$TARGET"` shape as src/lib/install-script.ts).
#   3. A file is removed only when its frontmatter declares exactly this
#      slug. A hand-written note that happens to sit at a candidate path is
#      reported and kept — a wrong deletion is unrecoverable, a missed one is
#      a follow-up command.
#
# Targets that install the pack as ONE file (cursor's router .mdc, the
# adapter JSONs for openai/langchain/mcp/...) have no per-skill file to
# remove; those refuse and name the file instead of pretending to work.
cmd_uninstall() {
  local slug="" target="$USB_TARGET" dry=0

  while [ $# -gt 0 ]; do
    case "$1" in
      --target=*) target="${1#--target=}" ;;
      --target)
        shift
        [ $# -gt 0 ] || { err "--target needs a value (e.g. --target claude)"; exit 2; }
        target="$1"
        ;;
      --dry-run)   dry=1 ;;
      --help|-h)   usage; exit 0 ;;
      --*)
        # install only WARNS on an unknown flag (it has always done that and
        # people pipe it), but here a typo like --taget=claude would silently
        # retarget a deletion — an unknown flag must stop the command.
        err "Unknown flag: $1"
        exit 2
        ;;
      *)
        if [ -n "$slug" ]; then
          err "uninstall takes one slug at a time ('$slug' and '$1')"
          err "run it once per skill:  usb uninstall $slug && usb uninstall $1"
          exit 2
        fi
        slug="$1"
        ;;
    esac
    shift
  done

  if [ -z "$slug" ]; then
    err "uninstall needs a skill slug"
    err "usage: usb uninstall <slug> [--target=<name>] [--dry-run]"
    exit 2
  fi
  case "$slug" in
    ''|*[!a-z0-9-]*)
      err "invalid slug: '$slug' (only lowercase letters, digits and dashes)"
      exit 2
      ;;
    -*)
      err "invalid slug: '$slug' (cannot start with a dash)"
      exit 2
      ;;
  esac
  if [ "${#slug}" -gt 64 ]; then
    err "invalid slug: '$slug' (max 64 characters)"
    exit 2
  fi

  local root="${AI_SKILL_HOME:-${HOME:-}/.ai-skills}"
  # DEFAULT_PACK.slug — the portable pack directory install always creates.
  local pack_slug="universal-skill-bridge-catalog"
  local pack_dir="$root/$pack_slug"

  # Resolve `auto` from what is on disk. Unlike install this never opens a
  # picker: a destructive command must not choose its target from a menu.
  # One runtime found -> use it; several -> demand --target; none -> the
  # same generic fallback install uses.
  if [ "$target" = "auto" ]; then
    local detected=()
    if [ -n "${LEOSIS_SKILLS_DIR:-}" ] || [ -d "${HOME:-}/.leosis" ];    then detected+=("leosis"); fi
    if [ -n "${CLAUDE_SKILLS_DIR:-}" ] || [ -d "${HOME:-}/.claude" ];    then detected+=("claude"); fi
    if [ -n "${HERMES_SKILLS_DIR:-}" ] || [ -d "${HOME:-}/.hermes" ];    then detected+=("hermes"); fi
    if [ -n "${CURSOR_RULES_DIR:-}" ]  || [ -d "${HOME:-}/.cursor" ];    then detected+=("cursor"); fi
    if [ -n "${OPENCODE_DIR:-}" ]      || [ -d "${HOME:-}/.opencode" ];  then detected+=("opencode"); fi
    if [ -n "${CODEX_HOME:-}" ]        || [ -d "${HOME:-}/.codex" ];     then detected+=("openai"); fi
    if [ -d "${HOME:-}/.continue" ];                                 then detected+=("langchain"); fi
    if [ -d "${HOME:-}/.aider" ];                                    then detected+=("anthropic"); fi
    if [ -d "${HOME:-}/.vllm" ];                                     then detected+=("vllm"); fi
    if [ -d "${HOME:-}/.ollama" ];                                   then detected+=("ollama"); fi
    if [ -d "${HOME:-}/.lmstudio" ];                                 then detected+=("lm-studio"); fi
    if [ -d "${HOME:-}/.openrouter" ];                               then detected+=("openrouter"); fi
    if [ -d "${HOME:-}/.groq" ];                                     then detected+=("groq"); fi
    if [ -d "${HOME:-}/.mistral" ];                                  then detected+=("mistral"); fi
    if [ -d "${HOME:-}/.mcp" ];                                      then detected+=("mcp"); fi

    local n="${#detected[@]}"
    if [ "$n" -eq 0 ]; then
      target="generic"
      info "No AI runtime detected — using the portable pack (generic), like install would."
    elif [ "$n" -eq 1 ]; then
      target="${detected[0]}"
      info "Auto-detected runtime: $target"
    else
      err "multiple runtimes detected on this machine: ${detected[*]}"
      err "uninstall never guesses where to delete — pass --target=<name>"
      exit 2
    fi
  fi

  # Candidate files for this target — the per-target layout install writes.
  local candidates=()
  local pack_level_file=""
  case "$target" in
    leosis)
      candidates+=("${LEOSIS_SKILLS_DIR:-${HOME:-}/.leosis/skills/$pack_slug}/$slug.md")
      ;;
    hermes)
      candidates+=("${HERMES_SKILLS_DIR:-${HOME:-}/.hermes/skills/$pack_slug}/$slug.md")
      ;;
    claude)
      # Both layouts are live: <=10 skills become <slug>/SKILL.md directories,
      # the full catalog lands as reference files under usb/skills/ beside the
      # router. Checking both lets either install shape be undone.
      candidates+=("${CLAUDE_SKILLS_DIR:-${HOME:-}/.claude/skills}/usb/skills/$slug.md")
      candidates+=("${CLAUDE_SKILLS_DIR:-${HOME:-}/.claude/skills}/$slug/SKILL.md")
      ;;
    cursor)
      pack_level_file="${CURSOR_RULES_DIR:-$PWD/.cursor/rules}/$pack_slug.mdc"
      ;;
    openai|anthropic|openrouter|groq|mistral|langchain|mcp)
      pack_level_file="$root/adapters/$target/$pack_slug.json"
      ;;
    ollama|lm-studio|vllm)
      candidates+=("$root/adapters/$target/$slug.md")
      ;;
    *)
      # generic, opencode and any unknown target install into the pack dir.
      candidates+=("$pack_dir/skills/$slug.md")
      ;;
  esac

  if [ -n "$pack_level_file" ]; then
    err "target '$target' installs the pack as ONE file, so a single skill cannot be pulled out of it."
    if [ -f "$pack_level_file" ]; then
      err "to drop the whole pack install from this target:"
      err "  rm \"$pack_level_file\""
    fi
    exit 2
  fi

  # The portable copy goes too: for generic it IS the install above (same
  # path, deduplicated below), for other targets it is the source cache that
  # `usb install` copies from — uninstall means the skill is gone from here.
  local portable="$pack_dir/skills/$slug.md"
  local c already=0
  for c in ${candidates[@]+"${candidates[@]}"}; do
    if [ "$c" = "$portable" ]; then already=1; fi
  done
  if [ "$already" = "0" ]; then candidates+=("$portable"); fi

  local removed=0 refused=0
  for c in ${candidates[@]+"${candidates[@]}"}; do
    if [ ! -e "$c" ]; then continue; fi

    if [ "$dry" = "1" ]; then
      info "[dry-run] would remove $c"
      removed=$((removed + 1))
      continue
    fi

    if ! grep -Eq "^slug: ${slug}[[:space:]]*\$" "$c" 2>/dev/null; then
      warn "kept $c — its frontmatter does not declare 'slug: $slug'"
      refused=$((refused + 1))
      continue
    fi

    rm -f "$c"
    ok "removed $c"
    removed=$((removed + 1))

    # Claude's small layout stores the file inside its own <slug>/ directory;
    # drop that directory too, but only when empty — anything the user kept
    # next to SKILL.md keeps it alive.
    case "$c" in
      */SKILL.md) rmdir "${c%/SKILL.md}" 2>/dev/null || true ;;
    esac
  done

  # The Claude router keeps an index of `slug1, slug2, ...` per category.
  # With a reference file gone, take the slug out of that list as well — an
  # index entry pointing at a deleted file is a broken skill. Four exact
  # forms (first/middle/last/alone), never a loose match, so a slug that is
  # the SUFFIX of another (`audit` inside `code-audit`) cannot be caught.
  if [ "$dry" != "1" ] && [ "$removed" -gt 0 ]; then
    local router="${CLAUDE_SKILLS_DIR:-${HOME:-}/.claude/skills}/usb/SKILL.md"
    if [ -f "$router" ]; then
      sed -i.bak \
        -e "s/, ${slug}, /, /g" \
        -e "s/^${slug}, //" \
        -e "s/, ${slug}\$//" \
        -e "s/^${slug}\$//" \
        "$router" || warn "could not refresh the Claude router index"
      rm -f "$router.bak"
      info "refreshed the Claude router index"
    fi
  fi

  # Keep the local registry honest — it counts what this pack has installed.
  local record="$pack_dir/installed.json"
  if [ "$dry" != "1" ] && [ "$removed" -gt 0 ] && [ -f "$record" ]; then
    local cur new
    cur=$(sed -n 's/.*"skill_count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$record" | head -n 1)
    if [ -n "$cur" ]; then
      if [ "$cur" -gt 0 ]; then new=$((cur - 1)); else new=0; fi
      if sed -i.bak "s/\"skill_count\"[[:space:]]*:[[:space:]]*${cur}/\"skill_count\": ${new}/" "$record"; then
        info "registry now reports $new skill(s) installed"
      else
        warn "could not update $record"
      fi
      rm -f "$record.bak"
    fi
  fi

  if [ "$dry" = "1" ]; then
    if [ "$removed" -gt 0 ]; then
      ok "dry-run: $removed file(s) would be removed for target '$target'"
      exit 0
    fi
    err "nothing to remove: no installed copy of '$slug' for target '$target'"
    exit 1
  fi

  if [ "$removed" -gt 0 ]; then
    if [ "$refused" -gt 0 ]; then
      warn "$refused file(s) kept because their frontmatter did not declare '$slug'"
    fi
    ok "uninstalled '$slug' from target '$target' ($removed file(s) removed)"
    exit 0
  fi

  if [ "$refused" -gt 0 ]; then
    err "nothing removed: $refused file(s) sit where '$slug' would live but do not declare it"
    err "remove them by hand if they are yours"
    exit 1
  fi
  err "no installed copy of '$slug' for target '$target' — checked:"
  for c in ${candidates[@]+"${candidates[@]}"}; do
    err "  $c"
  done
  err "install it first with:  usb install $slug --target=$target"
  exit 1
}

# --- pickers (used by interactive menu) --------------------------------------
pick_slug() {
  printf '\n  %sPick a single skill (type number, empty = cancel)%s\n\n' "$BOLD" "$RST" 2>/dev/null || printf '\n  Pick a single skill\n\n'
  local items
  items=$(curl -fsSL "$USB_BASE_URL/api/skills?target=generic" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for s in d["bundle"]["skills"]:
    slug = s["slug"]
    name = s.get("name", slug)
    print(f"{slug}|{name}")
' 2>/dev/null)
  if [ -z "$items" ]; then
    err "Could not load skills"
    return 1
  fi
  local i=1
  while IFS='|' read -r slug name; do
    printf '  %s%3d)%s %-44s' "$CYA" "$i" "$RST" "$slug"
    if [ $((i % 2)) -eq 0 ]; then printf '\n'; fi
    i=$((i+1))
  done <<< "$items"
  if [ $(( (i-1) % 2)) -ne 0 ]; then printf '\n'; fi
  printf '\n  Number: '
  local pick
  read -r pick
  pick="${pick%$'\r'}"
  [ -z "$pick" ] && return 1
  printf '%s\n' "$items" | sed -n "${pick}p" | cut -d'|' -f1
}

pick_domain() {
  printf '\n  Pick a domain\n\n'
  local items
  items=$(curl -fsSL "$USB_BASE_URL/api/skills?target=generic" | python3 -c '
import json, sys
d = json.load(sys.stdin)
seen = set()
order = []
for s in d["bundle"]["skills"]:
    slug = s["slug"]
    if "-" in slug:
        domain = slug.rsplit("-", 1)[0]
    else:
        domain = slug
    if domain not in seen:
        seen.add(domain)
        order.append((domain, s.get("name", domain)))
for domain, name in order:
    print(f"{domain}|{name}")
' 2>/dev/null)
  if [ -z "$items" ]; then
    err "Could not load domains"
    return 1
  fi
  local i=1
  while IFS='|' read -r slug name; do
    printf '  %s%3d)%s %-32s' "$CYA" "$i" "$RST" "$slug"
    if [ $((i % 3)) -eq 0 ]; then printf '\n'; fi
    i=$((i+1))
  done <<< "$items"
  if [ $(( (i-1) % 3)) -ne 0 ]; then printf '\n'; fi
  printf '\n  Number: '
  local pick
  read -r pick
  pick="${pick%$'\r'}"
  [ -z "$pick" ] && return 1
  printf '%s\n' "$items" | sed -n "${pick}p" | cut -d'|' -f1
}

pick_category() {
  printf '\n  Pick a category\n\n'
  local items
  items=$(curl -fsSL "$USB_BASE_URL/api/skills?target=generic" | python3 -c '
import json, sys, collections
d = json.load(sys.stdin)
counts = collections.Counter(s.get("category", "Other") for s in d["bundle"]["skills"])
for cat, n in counts.most_common():
    print(f"{cat}|{n}")
' 2>/dev/null)
  if [ -z "$items" ]; then
    err "Could not load categories"
    return 1
  fi
  local i=1
  while IFS='|' read -r cat n; do
    printf '  %s%3d)%s %-32s (%d skills)\n' "$CYA" "$i" "$RST" "$cat" "$n"
    i=$((i+1))
  done <<< "$items"
  printf '\n  Number: '
  local pick
  read -r pick
  pick="${pick%$'\r'}"
  [ -z "$pick" ] && return 1
  printf '%s\n' "$items" | sed -n "${pick}p" | cut -d'|' -f1
}

pick_preset() {
  printf '\n  Pick a preset\n\n'
  local presets=(
    "web-dev|React, Next.js, TypeScript, CSS, browser automation"
    "backend|Node.js, Python, FastAPI, SQL, Redis"
    "infra|Docker, Kubernetes, Terraform, GitHub Actions, AWS, Azure"
    "security|OAuth, RLS, prompt injection, secrets, hardening"
    "data|SQL, RAG, vector search, OpenAPI, GraphQL, prompt engineering"
    "core-only|Router + planner + memory + verification only (9 skills)"
  )
  local i=1
  for entry in "${presets[@]}"; do
    local slug="${entry%%|*}"
    local desc="${entry#*|}"
    printf '  %s%3d)%s %-14s %s%s%s\n' "$CYA" "$i" "$RST" "$slug" "$DIM" "$desc" "$RST"
    i=$((i+1))
  done
  printf '\n  Number: '
  local pick
  read -r pick
  pick="${pick%$'\r'}"
  [ -z "$pick" ] && return 1
  if ! [[ "$pick" =~ ^[0-9]+$ ]]; then return 1; fi
  local entry="${presets[$((pick-1))]}"
  printf '%s\n' "$entry" | cut -d'|' -f1
}

# --- interactive picker ------------------------------------------------------
cmd_interactive() {
  printf '\n'
  printf '  %s%s%s%susb%s %s— Universal Systems Bridge%s\n' \
    "$ACC" "$G_MARK" "$RST" "$BOLD" "$RST" "$DIM" "$RST"
  printf '  %splug-and-play skills for AI agents%s\n' "$DIM" "$RST"
  rule

  local version_json
  version_json=$(curl -fsSL "$USB_BASE_URL/api/version" 2>/dev/null) || {
    err "Cannot reach USB catalog at $USB_BASE_URL"
    exit 1
  }
  info "Catalog $USB_BASE_URL is reachable (pack $version_json)"

  local choices=(
    "Full pack (all 529 skills)"
    "Pick a single skill"
    "Pick a domain (65 domains x 8 workflows)"
    "Pick a category"
    "Pick a curated preset"
    "Just the 9 core skills"
    "Search by keyword"
    "Exit"
  )
  PS3=$'\n  Choose a number (1-8): '
  select opt in "${choices[@]}"; do
    REPLY="${REPLY%$'\r'}"
    case "$REPLY" in
      1)
        cmd_install
        return 0
        ;;
      2)
        local slug
        slug=$(pick_slug)
        if [ -n "$slug" ]; then
          cmd_install "$slug"
        fi
        return 0
        ;;
      3)
        local domain
        domain=$(pick_domain)
        if [ -n "$domain" ]; then
          cmd_install "$domain"
        fi
        return 0
        ;;
      4)
        local category
        category=$(pick_category)
        if [ -n "$category" ]; then
          cmd_install "$category"
        fi
        return 0
        ;;
      5)
        local preset
        preset=$(pick_preset)
        if [ -n "$preset" ]; then
          cmd_install "$preset"
        fi
        return 0
        ;;
      6)
        cmd_install core-only
        return 0
        ;;
      7)
        local query
        printf '\n  Search term: '
        read -r query
        query="${query%$'\r'}"
        if [ -n "$query" ]; then
          cmd_search "$query"
          printf '\n  Install one of these? Enter slug (or empty to skip): '
          local picked
          read -r picked
          picked="${picked%$'\r'}"
          if [ -n "$picked" ]; then
            cmd_install "$picked"
          fi
        fi
        return 0
        ;;
      8)
        printf '\n  Bye!\n\n'
        return 0
        ;;
      *)
        warn "Invalid choice"
        ;;
    esac
  done
}

# --- dispatcher --------------------------------------------------------------
[ $# -eq 0 ] && { cmd_interactive; exit 0; }

case "${1:-}" in
  install)        shift; cmd_install "$@" ;;
  uninstall)      shift; cmd_uninstall "$@" ;;
  i|interactive)  shift; cmd_interactive ;;
  search)         shift; cmd_search "$@" ;;
  info)           shift; cmd_info "$@" ;;
  list)           shift; cmd_list "$@" ;;
  secscan|scan)   shift; cmd_secscan "$@" ;;
  mcprobe)        shift
    command -v python3 >/dev/null 2>&1 || {
      err "mcprobe needs python3 in PATH"; exit 2; }
    MCP_DIR="$(cd "$(dirname "$USB_SECSCAN_ENGINE")" && pwd)"
    [ -f "$MCP_DIR/usb-mcprobe.py" ] || {
      err "mcprobe engine not found at $MCP_DIR/usb-mcprobe.py"
      err "the security engines ship with the npm package — npm install -g @peepsick/usb-cli"
      exit 2; }
    python3 "$(secscan_to_native_path "$MCP_DIR")/usb-mcprobe.py" "$@" ;;
  mcp)            shift
    command -v python3 >/dev/null 2>&1 || {
      err "mcp server needs python3 in PATH"; exit 2; }
    MCP_DIR="$(cd "$(dirname "$USB_SECSCAN_ENGINE")" && pwd)"
    [ -f "$MCP_DIR/usb-mcp.py" ] || {
      err "mcp server engine not found at $MCP_DIR/usb-mcp.py"
      err "the stdio MCP server ships with the npm package — npm install -g @peepsick/usb-cli"
      exit 2; }
    # `usb mcp` is the only command that needs a pip package (requirements.txt).
    # Without it, usb-mcp.py dies with a raw ModuleNotFoundError traceback on
    # the MCP client's stderr, which reads like a crashed server rather than a
    # missing dependency. Detect it up front and print the exact install
    # commands — including the PEP 668 form Debian/Ubuntu need, where a bare
    # `pip install mcp` is refused as an externally-managed environment.
    if ! python3 -c 'import mcp' >/dev/null 2>&1; then
      MCP_REQ="$(dirname "$MCP_DIR")/requirements.txt"
      err "the MCP stdio server needs the 'mcp' python package"
      [ -f "$MCP_REQ" ] && err "  python3 -m pip install -r \"$MCP_REQ\""
      err "  python3 -m pip install mcp"
      err "  Debian/Ubuntu (PEP 668): python3 -m pip install --break-system-packages mcp"
      err "  or run the server inside a virtualenv"
      exit 2
    fi
    python3 "$(secscan_to_native_path "$MCP_DIR")/usb-mcp.py" "$@" ;;
  version|-V|--version) cmd_version ;;
  -h|--help|help) usage ;;
  *)
    err "Unknown subcommand: $1"
    usage
    exit 1
    ;;
esac