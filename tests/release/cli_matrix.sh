#!/usr/bin/env bash
# v1.0.2 release-candidate CLI + Trust Layer e2e matrix against the LOCAL server.
# Run:  bash tmp-e2e/cli_matrix.sh
# Requires: the local app on :3000 with a seeded DB, git-bash (curl/python3).
set -u

BASE="${USB_BASE_URL:-http://127.0.0.1:3000}"
USB=(bash packages/cli/bin/usb.sh)
FAILED=0
PASSED=0

pass() { PASSED=$((PASSED+1)); echo "PASS $1"; }
fail() { FAILED=$((FAILED+1)); echo "FAIL $1  ${2:-}"; }

check_eq() { # name got want
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "got='$2' want='$3'"; fi
}

skill_count() { printf '%s' "$1" | sed -n 's/^SKILL_COUNT="\([0-9]*\)".*/\1/p' | head -1; }

export USB_BASE_URL="$BASE"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== E. CLI against local catalog =="
export NO_COLOR=1

out=$("${USB[@]}" version 2>&1); rc=$?
if [ $rc -eq 0 ] && printf '%s' "$out" | grep -q '"current"[[:space:]]*:[[:space:]]*"1\.0.2"'; then
  pass "E1 usb version -> current 1.0.2"
else fail "E1 usb version" "rc=$rc $(printf '%s' "$out" | head -3)"; fi

out=$("${USB[@]}" list 2>&1); rc=$?
n=$(printf '%s' "$out" | grep -c '  ')
if [ $rc -eq 0 ] && [ "$n" -ge 529 ]; then pass "E2 usb list -> >=529 lines"
else fail "E2 usb list" "rc=$rc lines=$n"; fi

out=$("${USB[@]}" search postgres 2>&1); rc=$?
if [ $rc -eq 0 ] && printf '%s' "$out" | grep -qi 'postgres' && ! printf '%s' "$out" | grep -qiE 'traceback|syntaxerror'; then
  pass "E3 usb search postgres"
else fail "E3 usb search" "rc=$rc $(printf '%s' "$out" | head -3)"; fi

out=$("${USB[@]}" info intent-router 2>&1); rc=$?
if [ $rc -eq 0 ] && printf '%s' "$out" | grep -qi 'intent-router'; then pass "E4 usb info intent-router"
else fail "E4 usb info" "rc=$rc"; fi

out=$("${USB[@]}" install intent-router --dry-run 2>&1); rc=$?
[ $rc -eq 0 ] && check_eq "E5 dry-run slug -> 1 skill" "$(skill_count "$out")" "1" || fail "E5 dry-run slug" "rc=$rc"

out=$("${USB[@]}" install web-dev --dry-run 2>&1); rc=$?
[ $rc -eq 0 ] && check_eq "E6 dry-run preset -> 73" "$(skill_count "$out")" "73" || fail "E6 dry-run preset" "rc=$rc"

out=$("${USB[@]}" install react-state --dry-run 2>&1); rc=$?
[ $rc -eq 0 ] && check_eq "E7 dry-run DOMAIN via bare arg -> 8 (new fallback)" "$(skill_count "$out")" "8" || fail "E7 dry-run domain" "rc=$rc $(printf '%s' "$out"|head -2)"

out=$("${USB[@]}" install react-state-audit --dry-run 2>&1); rc=$?
[ $rc -eq 0 ] && check_eq "E8 dry-run single domain-skill -> 1" "$(skill_count "$out")" "1" || fail "E8 dry-run domain-skill" "rc=$rc"

out=$("${USB[@]}" install react --dry-run 2>&1); rc=$?
[ $rc -eq 0 ] && check_eq "E9 bare prefix 'react' is NOT a domain -> 0" "$(skill_count "$out")" "0" || fail "E9 prefix not a domain" "rc=$rc"

out=$(printf '8\r\n' | "${USB[@]}" i 2>&1); rc=$?
if [ $rc -eq 0 ] && printf '%s' "$out" | grep -q "Bye!"; then pass "E10 interactive menu accepts CRLF"
else fail "E10 interactive CRLF" "rc=$rc $(printf '%s' "$out" | tail -2)"; fi

echo "== F. real install into a fake HOME, then scan it =="
FAKE="$WORK/home"; mkdir -p "$FAKE"
url="$BASE/api/install?target=claude&slug=intent-router"
curl -fsSL "$url" -o "$WORK/install.sh" || fail "F1 download installer"
HOME="$FAKE" USERPROFILE="$FAKE" bash "$WORK/install.sh" > "$WORK/install.log" 2>&1
rc=$?
if [ $rc -eq 0 ] && [ -f "$FAKE/.claude/skills/intent-router/SKILL.md" ]; then
  pass "F1 claude install lands ~/.claude/skills/<slug>/SKILL.md"
else fail "F1 claude install" "rc=$rc log=$(tail -3 "$WORK/install.log")"; fi
[ -f "$FAKE/.ai-skills/universal-skill-bridge-catalog/installed.json" ] \
  && pass "F2 local registry installed.json written" \
  || fail "F2 installed.json missing"

out=$("${USB[@]}" secscan "$FAKE/.claude/skills" 2>&1); rc=$?
if { [ $rc -eq 0 ] || [ $rc -eq 1 ]; } && printf '%s' "$out" | grep -q "skill(s) verified"; then
  pass "F3 secscan of installed tree (exit $rc, honest verdicts)"
else fail "F3 secscan installed" "rc=$rc $(printf '%s' "$out" | tail -3)"; fi

out=$("${USB[@]}" secscan --json "$FAKE/.claude/skills" 2>&1); rc=$?
if [ $rc -eq 0 ] || [ $rc -eq 1 ]; then
  if printf '%s' "$out" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    pass "F4 secscan --json parses"
  else fail "F4 secscan --json invalid JSON" "$(printf '%s' "$out" | head -2)"; fi
else fail "F4 secscan --json rc" "rc=$rc"; fi

"${USB[@]}" secscan --write-baseline "$WORK/base.json" "$FAKE/.claude/skills" >/dev/null 2>&1
out=$("${USB[@]}" secscan --baseline "$WORK/base.json" "$FAKE/.claude/skills" 2>&1); rc=$?
[ $rc -eq 0 ] && pass "F5 baseline acknowledges existing findings (exit 0)" \
  || fail "F5 baseline" "rc=$rc $(printf '%s' "$out" | tail -2)"

echo "== G. mcprobe consent + live stub observation =="
STUB="$WORK/stub"; mkdir -p "$STUB"
cat > "$STUB/server.py" <<'PY'
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "stub-notes", "version": "0.0.1"}}}) + "\n")
    elif method == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "create_note", "description": "Create a note",
             "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}}}]}}) + "\n")
    elif method and method.startswith("notifications/"):
        pass
    elif mid is not None:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                     "error": {"code": -32601, "message": "method not found"}}) + "\n")
    sys.stdout.flush()
PY
# Launch the stub with a real interpreter: on Windows, `python3` can be the
# Python Install Manager alias, which under mcprobe's minimized env prepends
# "Extracting: ..." to stdout (a protocol error, correctly detected — but it
# would mask what this test is actually about). The script path must be
# absolute: mcprobe only sets cwd when an arg IS a directory, so it spawns the
# child in ITS cwd — a relative arg would be looked up in the repo, not here.
PYBIN="$(command -v python3 2>/dev/null)"
case "$PYBIN" in *[Ww]indows[Aa]pps*) PYBIN="$(command -v python 2>/dev/null)" ;; esac
STUBM="$STUB"
if command -v cygpath >/dev/null 2>&1; then
  PYBIN="$(cygpath -m "$PYBIN")"
  STUBM="$(cygpath -m "$STUB")"
fi
printf '{"mcpServers": {"notes": {"command": "%s", "args": ["%s"]}}}\n' \
  "$PYBIN" "$STUBM/server.py" > "$STUB/.mcp.json"

out=$(printf '' | "${USB[@]}" mcprobe "$STUB" 2>&1); rc=$?
if [ $rc -eq 2 ] && printf '%s' "$out" | grep -qi "nothing was contacted\|dry"; then
  pass "G1 mcprobe without --yes contacts nothing (exit 2)"
else fail "G1 mcprobe consent refusal" "rc=$rc $(printf '%s' "$out" | tail -2)"; fi

out=$("${USB[@]}" mcprobe --yes --timeout 15 "$STUB" 2>"$WORK/mcprobe.err"); rc=$?
printf '%s' "$out" > "$WORK/g2.json"
if [ $rc -eq 0 ] && python3 - "$WORK/g2.json" <<'PY' 2>"$WORK/assert.err"
import json, sys
s = open(sys.argv[1], encoding="utf-8").read()
start = s.find("{")
assert start >= 0, f"no JSON in output: {s[:160]!r}"
r = json.loads(s[start:])
assert r["schema"] == "usb-mcprobe-report/v1", r["schema"]
rep = r["reports"][0]
assert rep["transport"] == "stdio", rep["transport"]
assert rep["handshake"]["initialize"] == "ok" and rep["handshake"]["tools_list"] == "ok", rep["handshake"]
assert [t["name"] for t in rep["tools"]] == ["create_note"], rep["tools"]
assert rep["observation"]["complete"] is True, rep["observation"]
assert r["summary"] == {"failed": 0, "probed": 1}, r["summary"]
PY
then
  pass "G2 mcprobe observes the stub server (handshake + tools, no tool call)"
else
  fail "G2 mcprobe observation" "rc=$rc out=$(head -c 300 "$WORK/g2.json" 2>/dev/null) assert=$(head -c 300 "$WORK/assert.err" 2>/dev/null) spawn=$(head -c 200 "$WORK/mcprobe.err" 2>/dev/null)"
fi

echo "== H. stdio MCP server wiring via the CLI =="
tools=$(printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | timeout 25 "${USB[@]}" mcp 2>"$WORK/mcp.err" | python3 -c "
import sys, json
last = None
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    m = json.loads(line)
    if m.get('id') == 2: last = m
print(','.join(sorted(t['name'] for t in last['result']['tools'])))
" 2>>"$WORK/mcp.err")
check_eq "H1 usb mcp tools/list -> usb_scan,usb_mcprobe" "$tools" "usb_mcprobe,usb_scan"

echo
echo "CLI matrix: passed=$PASSED failed=$FAILED"
[ "$FAILED" -eq 0 ] && echo "ALL CLI TESTS PASSED" || { echo "FAILURES PRESENT"; exit 1; }
