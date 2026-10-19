#!/usr/bin/env bash
# Offline regression suite for `usb uninstall` (packages/cli/bin/usb.sh).
#
# No network and no catalog: the command is local by design, so every case
# runs against a sandboxed HOME with layouts synthesised exactly the way
# src/lib/install-script.ts writes them. What is locked here:
#   - the three safety rules (slug charset, layout-only candidate paths,
#     frontmatter must declare the slug before anything is removed)
#   - per-target path mapping: claude (both layouts), leosis/hermes flat,
#     the portable pack (generic), adapter-dir targets
#   - Claude router re-index + installed.json skill_count decrement
#   - `auto` refusing to guess between multiple runtimes (destructive +
#     ambiguous = stop and ask), single-runtime and none -> generic
#   - pack-level targets (cursor .mdc, adapter JSONs) refusing with the
#     exact file instead of silently doing nothing
#   - `--target <name>` in the space form, and --dry-run removing nothing
#   - the interactive menu's uninstall entry (pick runtime -> pick skill ->
#     confirm): the same guardrails, prompts on stderr so the UI survives
#     the $( ) capture, and cancel keeping everything on disk
#   - exit codes: 0 removed, 1 nothing found/kept, 2 usage or safety stop
#
# Deliberately no `set -e`: most cases ASSERT a non-zero exit status.
set -u

USB_SH="$(cd "$(dirname "$0")/.." && pwd)/packages/cli/bin/usb.sh"
FAILED=0
LAST_OUT=""

run() { # run <expected_rc> <description> [--] <command...>
  local want="$1" desc="$2"
  shift 2
  # An optional `--` separator keeps call sites readable; it is not a command.
  [ "${1:-}" = "--" ] && shift
  local out rc
  out=$("$@" 2>&1)
  rc=$?
  LAST_OUT="$out"
  if [ "$rc" = "$want" ]; then
    printf '  ok   %s\n' "$desc"
  else
    printf '  FAIL %s (want rc=%s, got rc=%s)\n' "$desc" "$want" "$rc"
    printf '%s\n' "$out" | sed 's/^/         | /'
    FAILED=1
  fi
}

contains() { # contains <description> <substring> (uses LAST_OUT)
  if printf '%s' "$LAST_OUT" | grep -qF -- "$2"; then
    printf '  ok   %s\n' "$1"
  else
    printf '  FAIL %s (output missing: %s)\n' "$1" "$2"
    printf '%s\n' "$LAST_OUT" | sed 's/^/         | /'
    FAILED=1
  fi
}

lacks() { # lacks <description> <substring> (uses LAST_OUT)
  if printf '%s' "$LAST_OUT" | grep -qF -- "$2"; then
    printf '  FAIL %s (output should not contain: %s)\n' "$1" "$2"
    FAILED=1
  else
    printf '  ok   %s\n' "$1"
  fi
}

present() { # present <path>
  if [ -e "$1" ]; then
    printf '  ok   exists: %s\n' "${1#$HOME/}"
  else
    printf '  FAIL expected file to exist: %s\n' "${1#$HOME/}"
    FAILED=1
  fi
}

absent() { # absent <path>
  if [ -e "$1" ]; then
    printf '  FAIL expected file to be gone: %s\n' "${1#$HOME/}"
    FAILED=1
  else
    printf '  ok   gone: %s\n' "${1#$HOME/}"
  fi
}

# --- fixtures -----------------------------------------------------------------
SKILL_SLUGS="intent-router react-state-build"

write_skill() { # write_skill <path> <slug>
  mkdir -p "$(dirname "$1")" || return 1
  cat > "$1" <<EOF
---
description: "Fixture skill for the uninstall suite"
slug: $2
name: $2
category: testing
---

# Fixture skill

Protocol body for $2.
EOF
}

seed_claude_catalog() {
  # What `usb install --target=claude` writes for a full catalog: reference
  # files under usb/skills/ next to the router skill.
  local dest="$HOME/.claude/skills"
  write_skill "$dest/usb/skills/intent-router.md" intent-router
  write_skill "$dest/usb/skills/react-state-build.md" react-state-build
  mkdir -p "$dest/usb"
  cat > "$dest/usb/SKILL.md" <<'EOF'
---
description: "Router"
---

# Router index

### testing
intent-router, intent-router-extras, react-state-build
EOF
}

seed_portable() {
  local pack="$AI_SKILL_HOME/universal-skill-bridge-catalog"
  write_skill "$pack/skills/intent-router.md" intent-router
  write_skill "$pack/skills/react-state-build.md" react-state-build
  cat > "$pack/installed.json" <<'EOF'
{
  "pack": "universal-skill-bridge-catalog",
  "version": "1.0.5",
  "target": "generic",
  "skill_count": 529,
  "installed_at": "2026-10-19T00:00:00Z"
}
EOF
}

new_sandbox() {
  SB="$SB_ROOT/case-$((CASE_NO++))"
  export HOME="$SB/home"
  export AI_SKILL_HOME="$SB/ai-skills"
  mkdir -p "$HOME" "$AI_SKILL_HOME"
  unset LEOSIS_SKILLS_DIR CLAUDE_SKILLS_DIR HERMES_SKILLS_DIR CURSOR_RULES_DIR \
        OPENCODE_DIR CODEX_HOME 2>/dev/null || true
}

SB_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/usb-uninstall-suite.XXXXXXXX") || exit 1
trap 'rm -rf "$SB_ROOT"' EXIT
CASE_NO=0
export NO_COLOR=1

printf 'usb uninstall — offline safety + layout suite\n'

# --- A. usage and input safety: exit 2, nothing on disk touched ---------------
printf '\nA. usage and input safety\n'
new_sandbox
seed_claude_catalog
seed_portable
A_CAT="$HOME/.claude/skills/usb/skills/intent-router.md"

run 0 "A0 --help prints usage" -- bash "$USB_SH" uninstall --help
contains "A0 help documents uninstall" "usb uninstall <slug>"

run 2 "A1 no slug -> usage error" -- bash "$USB_SH" uninstall
contains "A1 says what usage is" "usage: usb uninstall"
run 2 "A2 path traversal rejected" -- bash "$USB_SH" uninstall "../evil"
contains "A2 names the problem" "invalid slug"
run 2 "A3 leading dash rejected" -- bash "$USB_SH" uninstall "-leading"
run 2 "A4 two slugs in one call rejected" -- bash "$USB_SH" uninstall intent-router react-state-build
contains "A4 explains one-at-a-time" "one slug at a time"
run 2 "A5 unknown flag (typo'd --target) stops the command" -- \
  bash "$USB_SH" uninstall intent-router --taget=claude
contains "A5 names the flag" "Unknown flag"
run 2 "A6 --target without a value stops" -- bash "$USB_SH" uninstall intent-router --target
contains "A6 asks for a value" "--target needs a value"

present "$A_CAT"
present "$AI_SKILL_HOME/universal-skill-bridge-catalog/skills/intent-router.md"

# --- B. claude catalog layout: removal, neighbours, frontmatter guard ---------
printf '\nB. claude catalog layout (reference files + router)\n'
# A decoy at the SMALL-layout candidate path with someone else's slug: the
# layout says the path would be ours, the file says it is not — it must stay.
write_skill "$HOME/.claude/skills/intent-router/SKILL.md" some-other-skill
B_DECOY="$HOME/.claude/skills/intent-router/SKILL.md"
B_NEIGHBOUR="$HOME/.claude/skills/usb/skills/react-state-build.md"
B_ROUTER="$HOME/.claude/skills/usb/SKILL.md"

run 0 "B1 catalog-layout file removed" -- \
  bash "$USB_SH" uninstall intent-router --target=claude
absent "$A_CAT"
present "$B_NEIGHBOUR"
present "$B_DECOY"
contains "B2 decoy was reported as kept" "frontmatter does not declare"
absent "$AI_SKILL_HOME/universal-skill-bridge-catalog/skills/intent-router.md"

# Router index: the removed slug leaves as an ENTRY while the prefix-cousin
# (`intent-router-extras`) must stay — a plain substring grep would count the
# cousin as a stale match, so this anchors on the entry's real delimiters.
if grep -qE '(^|, )intent-router(, |$)' "$B_ROUTER" 2>/dev/null; then
  printf '  FAIL B3 router index kept a stale intent-router entry\n'
  FAILED=1
else
  printf '  ok   B3 router index re-indexed (stale entry gone)\n'
fi
if grep -qF "intent-router-extras" "$B_ROUTER" 2>/dev/null \
   && grep -qF "react-state-build" "$B_ROUTER" 2>/dev/null; then
  printf '  ok   B4 prefix-cousin and neighbour survived the re-index\n'
else
  printf '  FAIL B4 re-index removed too much\n'
  FAILED=1
fi

# installed.json must count one fewer skill.
if grep -q '"skill_count": 528' "$AI_SKILL_HOME/universal-skill-bridge-catalog/installed.json" 2>/dev/null; then
  printf '  ok   B5 registry decremented 529 -> 528\n'
else
  printf '  FAIL B5 registry not decremented\n'
  sed 's/^/         | /' "$AI_SKILL_HOME/universal-skill-bridge-catalog/installed.json" 2>/dev/null
  FAILED=1
fi

# The decoy from B2 is still there, so the second run's honest answer is
# "something sits at the path but it is not ours" — not a bare not-found.
run 1 "B6 second run removes nothing (rc=1, not success)" -- \
  bash "$USB_SH" uninstall intent-router --target=claude
contains "B6 explains the kept file instead of claiming success" "nothing removed"

# --- C. claude small layout: <slug>/SKILL.md dir, space-form --target ---------
printf '\nC. claude small layout (one skill per directory)\n'
new_sandbox
write_skill "$HOME/.claude/skills/intent-router/SKILL.md" intent-router
write_skill "$HOME/.claude/skills/react-state-build/SKILL.md" react-state-build
C_DIR="$HOME/.claude/skills/intent-router"

run 0 "C1 removed via space-form --target claude" -- \
  bash "$USB_SH" uninstall intent-router --target claude
absent "$C_DIR/SKILL.md"
if [ -d "$C_DIR" ]; then
  printf '  FAIL C2 empty skill directory not pruned\n'
  FAILED=1
else
  printf '  ok   C2 empty skill directory pruned\n'
fi
present "$HOME/.claude/skills/react-state-build/SKILL.md"

# --- D. leosis flat layout + portable-only (generic) --------------------------
printf '\nD. leosis flat layout and the portable pack\n'
new_sandbox
write_skill "$HOME/.leosis/skills/universal-skill-bridge-catalog/intent-router.md" intent-router
write_skill "$HOME/.leosis/skills/universal-skill-bridge-catalog/react-state-build.md" react-state-build
D_FLAT="$HOME/.leosis/skills/universal-skill-bridge-catalog/intent-router.md"

run 0 "D1 leosis flat file removed" -- bash "$USB_SH" uninstall intent-router --target=leosis
absent "$D_FLAT"
present "$HOME/.leosis/skills/universal-skill-bridge-catalog/react-state-build.md"

new_sandbox
seed_portable
run 0 "D2 generic = the portable copy itself" -- \
  bash "$USB_SH" uninstall intent-router --target=generic
absent "$AI_SKILL_HOME/universal-skill-bridge-catalog/skills/intent-router.md"
present "$AI_SKILL_HOME/universal-skill-bridge-catalog/skills/react-state-build.md"

# --- E. dry-run removes nothing -----------------------------------------------
printf '\nE. dry-run\n'
new_sandbox
seed_claude_catalog
E_CAT="$HOME/.claude/skills/usb/skills/intent-router.md"
run 0 "E1 dry-run exits 0 and reports intent" -- \
  bash "$USB_SH" uninstall intent-router --target=claude --dry-run
contains "E2 says what it would do" "[dry-run] would remove"
present "$E_CAT"

# --- F. pack-level targets refuse with the exact file -------------------------
printf '\nF. pack-level targets (single-file installs)\n'
new_sandbox
F_JSON="$AI_SKILL_HOME/adapters/openai/universal-skill-bridge-catalog.json"
mkdir -p "$(dirname "$F_JSON")"
printf '{"skills": ["intent-router"]}\n' > "$F_JSON"
run 2 "F1 adapter target refuses (rc=2)" -- \
  bash "$USB_SH" uninstall intent-router --target=openai
contains "F2 names the single file" "$F_JSON"
present "$F_JSON"

new_sandbox
F_MDC="$HOME/.cursor/rules/universal-skill-bridge-catalog.mdc"
mkdir -p "$(dirname "$F_MDC")"
printf -- '---\nalwaysApply: false\n---\n' > "$F_MDC"
run 2 "F3 cursor router refuses (rc=2, auto-detected target)" -- \
  bash "$USB_SH" uninstall intent-router
contains "F4 explains one-file install" "ONE file"
present "$F_MDC"

# --- G. auto-detect: one runtime works, several refuse to guess ---------------
printf '\nG. auto target resolution\n'
new_sandbox
seed_claude_catalog
G_CAT="$HOME/.claude/skills/usb/skills/intent-router.md"
run 0 "G1 sole runtime detected -> used automatically" -- \
  bash "$USB_SH" uninstall intent-router
absent "$G_CAT"

new_sandbox
seed_claude_catalog
write_skill "$HOME/.leosis/skills/universal-skill-bridge-catalog/intent-router.md" intent-router
G_CAT="$HOME/.claude/skills/usb/skills/intent-router.md"
G_LEO="$HOME/.leosis/skills/universal-skill-bridge-catalog/intent-router.md"
run 2 "G2 two runtimes -> refuse to guess (rc=2)" -- \
  bash "$USB_SH" uninstall intent-router
contains "G3 asks for --target explicitly" "pass --target=<name>"
present "$G_CAT"
present "$G_LEO"

new_sandbox
seed_portable
run 0 "G4 no runtime -> generic fallback, portable copy removed" -- \
  bash "$USB_SH" uninstall intent-router
absent "$AI_SKILL_HOME/universal-skill-bridge-catalog/skills/intent-router.md"

# --- H. frontmatter guard on the portable copy --------------------------------
printf '\nH. frontmatter guard\n'
new_sandbox
P="$AI_SKILL_HOME/universal-skill-bridge-catalog/skills/intent-router.md"
mkdir -p "$(dirname "$P")"
cat > "$P" <<'EOF'
---
slug: something-else
name: something-else
---
Hand-written file that happens to sit at a candidate path.
EOF
run 1 "H1 file without our slug is kept, rc=1" -- \
  bash "$USB_SH" uninstall intent-router --target=generic
contains "H2 says why it was kept" "frontmatter does not declare"
present "$P"

# --- I. adapter-dir targets (ollama family) remove per-skill files ------------
printf '\nI. ollama / lm-studio / vllm adapter dirs\n'
new_sandbox
write_skill "$AI_SKILL_HOME/adapters/ollama/intent-router.md" intent-router
write_skill "$AI_SKILL_HOME/adapters/ollama/react-state-build.md" react-state-build
run 0 "I1 adapter-dir skill removed" -- \
  bash "$USB_SH" uninstall intent-router --target=ollama
absent "$AI_SKILL_HOME/adapters/ollama/intent-router.md"
present "$AI_SKILL_HOME/adapters/ollama/react-state-build.md"

# --- J. genuinely nothing installed: the plain not-found path ------------------
printf '\nJ. nothing installed anywhere\n'
new_sandbox
run 1 "J1 no files at all -> rc=1" -- \
  bash "$USB_SH" uninstall intent-router --target=claude
contains "J2 plain not-found message" "no installed copy"
contains "J3 shows which paths were checked" "checked:"
contains "J4 offers the install command" "usb install intent-router --target=claude"

# --- K. interactive menu: uninstall entry --------------------------------------
printf '\nK. interactive menu uninstall entry\n'

# The interactive start greets via the catalog; a file:// stub answers
# /api/version so this stays fully offline. The curl on PATH may be Windows'
# native build (it resolves no MSYS paths), so there the stub URL is spelled
# with a drive letter; on Linux file://$SB_ROOT is already /tmp/...
MENU_STUB="$SB_ROOT/menu-stub"
mkdir -p "$MENU_STUB/api"
printf '{"ok":true,"current":"test"}\n' > "$MENU_STUB/api/version"
if command -v cygpath >/dev/null 2>&1; then
  export USB_BASE_URL="file:///$(cygpath -m "$MENU_STUB")"
else
  export USB_BASE_URL="file://$MENU_STUB"
fi

run_menu() { # run_menu <expected_rc> <description> <stdin input>
  local want="$1" desc="$2" input="$3"
  local out rc
  out=$(printf '%s' "$input" | bash "$USB_SH" interactive 2>&1)
  rc=$?
  LAST_OUT="$out"
  if [ "$rc" = "$want" ]; then
    printf '  ok   %s\n' "$desc"
  else
    printf '  FAIL %s (want rc=%s, got rc=%s)\n' "$desc" "$want" "$rc"
    printf '%s\n' "$out" | sed 's/^/         | /'
    FAILED=1
  fi
}

# K1: two runtimes on disk -> the menu asks which one, then which skill,
# then confirms; the removal itself is cmd_uninstall's (guardrails intact).
new_sandbox
write_skill "$HOME/.claude/skills/intent-router/SKILL.md" intent-router
write_skill "$HOME/.claude/skills/react-state-build/SKILL.md" react-state-build
write_skill "$HOME/.leosis/skills/universal-skill-bridge-catalog/intent-router.md" intent-router
K_CLAUDE="$HOME/.claude/skills/intent-router/SKILL.md"
K_LEO="$HOME/.leosis/skills/universal-skill-bridge-catalog/intent-router.md"

run_menu 0 "K1 menu removes the picked skill (8 -> runtime 1 -> skill 1 -> y)" \
  $'8\n1\n1\ny\n'
contains "K2 menu shows the runtime picker" "Which runtime"
absent "$K_CLAUDE"
present "$K_LEO"
present "$HOME/.claude/skills/react-state-build/SKILL.md"
contains "K3 confirms via cmd_uninstall's own summary" \
  "uninstalled 'intent-router' from target 'claude'"

# K4: answering N cancels and nothing moves.
new_sandbox
write_skill "$HOME/.leosis/skills/universal-skill-bridge-catalog/intent-router.md" intent-router
K_KEEP="$HOME/.leosis/skills/universal-skill-bridge-catalog/intent-router.md"
run_menu 0 "K4 confirm=N -> cancelled, rc=0" $'8\n1\nn\n'
contains "K5 says it cancelled" "Cancelled — nothing removed."
present "$K_KEEP"

# K6: the menu's list is what is on disk, but a file that only LOOKS like
# ours (wrong frontmatter slug) is still refused by the shared guardrail.
new_sandbox
write_skill "$HOME/.claude/skills/intent-router/SKILL.md" some-other-skill
K_DECOY="$HOME/.claude/skills/intent-router/SKILL.md"
run_menu 1 "K6 decoy through the menu -> refused, rc=1" $'8\n1\ny\n'
contains "K7 explains the refusal" "frontmatter does not declare"
present "$K_DECOY"

# K8: the router is infrastructure, never a pickable skill; an empty answer
# just leaves the menu.
new_sandbox
seed_claude_catalog
run_menu 0 "K8 router not offered, EOF leaves cleanly" $'8\n'
contains "K9 lists real skills" "1) intent-router"
if printf '%s\n' "$LAST_OUT" | grep -qE '^ +[0-9]+\) usb$'; then
  printf '  FAIL K10 the router itself was offered for uninstall\n'
  FAILED=1
else
  printf '  ok   K10 the router is not in the list\n'
fi

# K12: nothing installed anywhere -> a friendly message, not an error.
new_sandbox
run_menu 0 "K12 empty machine -> friendly no-op" $'8\n'
contains "K13 says there is nothing to uninstall" "Nothing installed yet"

# --- summary ------------------------------------------------------------------
printf '\n'
if [ "$FAILED" = "0" ]; then
  printf 'ALL CHECKS PASSED\n'
  exit 0
fi
printf 'CHECKS FAILED\n'
exit 1
