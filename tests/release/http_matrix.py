"""v1.0.3 release-candidate HTTP test matrix against the local server.

Run:  python3 tmp-e2e/http_matrix.py http://127.0.0.1:3000
Exit 0 = all pass. Prints a PASS/FAIL line per check plus a summary.
"""
import hashlib
import json
import re
import sys
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:3000"
FAILURES = []
PASSES = 0


def get(path, raw=False):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        body = r.read()
        return body if raw else body.decode("utf-8")


def post_json(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def check(name, ok, detail=""):
    global PASSES
    if ok:
        PASSES += 1
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}  {detail}")


def skill_count(text):
    m = re.search(r'SKILL_COUNT="(\d+)"', text)
    return int(m.group(1)) if m else None


def report(name, status, expected_exit=None):
    ok = status == expected_exit
    check(name, ok, f"exit {status}, expected {expected_exit}")
    return ok


# --- A. health & version -----------------------------------------------------
health = json.loads(get("/api/health"))
check("A1 health ok", health.get("ok") is True, str(health))

ver = json.loads(get("/api/version"))
check("A2 version current=1.0.3", ver.get("current") == "1.0.3", str(ver.get("current")))
check("A3 version has minSupported + update fields",
      all(k in ver for k in ("latest", "minSupported", "updateAvailable")),
      ",".join(sorted(ver.keys())))

# --- B. catalog & filters ----------------------------------------------------
full = json.loads(get("/api/skills?target=generic"))["bundle"]
skills = full["skills"]
slugs_all = [s["slug"] for s in skills]
check("B1 catalog = 529 skills", len(skills) == 529, str(len(skills)))
pack = full.get("pack") or {}
check("B2 pack version 1.0.3", pack.get("version") == "1.0.3", str(pack))
check("B3 bundle schemaVersion skill-bridge/v1",
      full.get("schemaVersion") == "skill-bridge/v1", str(full.get("schemaVersion")))
check("B4 slugs unique", len(set(slugs_all)) == len(slugs_all),
      f"{len(slugs_all)} vs {len(set(slugs_all))} unique")


def filtered(params):
    return [s["slug"] for s in json.loads(
        get("/api/skills?target=generic&" + params))["bundle"]["skills"]]


web_dev = filtered("preset=web-dev")
check("B5 preset=web-dev -> 73", len(web_dev) == 73, str(len(web_dev)))
core_only = filtered("preset=core-only")
check("B6 preset=core-only -> 9", len(core_only) == 9, str(len(core_only)))
dom = filtered("domain=react-state")
check("B7 domain=react-state -> 8", len(dom) == 8, str(len(dom)))
check("B8 domain skills all under react-state-",
      all(s.startswith("react-state-") for s in dom), ",".join(dom))
one = filtered("slug=intent-router")
check("B9 slug=intent-router -> 1", one == ["intent-router"], str(one))
fb = filtered("slug=react-state")
check("B10 FALLBACK slug=react-state -> same 8 as domain=", sorted(fb) == sorted(dom),
      f"{sorted(fb)} vs {sorted(dom)}")
audit = filtered("category=Audit")
expected_audit = len([s for s in skills if s["category"] == "Audit"])
check("B11 category=Audit self-consistent", len(audit) == expected_audit,
      f"{len(audit)} vs {expected_audit}")
comb = filtered("preset=backend&exclude=python-*")
bad = [s for s in comb if s.startswith("python-")]
check("B12 combo preset&exclude has no python-*", not bad, ",".join(bad))
check("B13 typo slug -> empty (fallback cannot invent)",
      filtered("slug=intent-routee") == [], "non-empty")
typo_domain = filtered("slug=react")
check("B14 slug=react (no such domain/skill) -> empty", typo_domain == [], str(typo_domain))

# --- C. install script + sha256 parity --------------------------------------
combos = [
    ("full pack", ""),
    ("preset web-dev", "&preset=web-dev"),
    ("preset core-only", "&preset=core-only"),
    ("domain react-state", "&domain=react-state"),
    ("SLUG FALLBACK react-state", "&slug=react-state"),
    ("slug intent-router", "&slug=intent-router"),
    ("category Audit", "&category=Audit"),
    ("core=true", "&core=true"),
    ("core+slug additive", "&slug=intent-router&core=true"),
    ("combo data+exclude", "&preset=data&exclude=python-*"),
    ("fallback+category", "&slug=react-state&category=Audit"),
]
expected_counts = {
    "": 529,
    "&preset=web-dev": 73,
    "&preset=core-only": 9,
    "&domain=react-state": 8,
    "&slug=react-state": 8,
    "&slug=intent-router": 1,
    "&core=true": 9,
    "&slug=intent-router&core=true": 9,
    "&slug=react-state&category=Audit": 1,  # intersection: only react-state-audit
}
fallback_skills = None
for label, q in combos:
    script = get(f"/api/install?target=generic{q}", raw=True)
    text = script.decode("utf-8", "replace")
    want = expected_counts.get(q)
    n = skill_count(text)
    if want is not None:
        check(f"C install count [{label}] == {want}", n == want, f"got {n}")
    else:
        check(f"C install count [{label}] present", n is not None, "no (N skills) marker")
    got_hash = get(f"/api/install-sha256?target=generic{q}").strip()
    local = hashlib.sha256(script).hexdigest()
    check(f"C sha256 parity [{label}]", local == got_hash, f"{local[:12]} != {got_hash[:12]}")
    check(f"C script shebang [{label}]", text.startswith("#!/usr/bin/env bash"), text[:40])
    check(f"C no error text [{label}]", "could not be generated" not in text, "error script")
    # remember the skill set of the fallback install for set-parity vs domain=
    if q == "&slug=react-state":
        fallback_skills = set(re.findall(r"slug[\"']?\s*[:=]\s*[\"']([a-z0-9-]+)[\"']", text))
    if q == "&domain=react-state":
        domain_skills = set(re.findall(r"slug[\"']?\s*[:=]\s*[\"']([a-z0-9-]+)[\"']", text))

if fallback_skills and domain_skills:
    check("C fallback slug set == domain set (skills embedded in script)",
          fallback_skills == domain_skills,
          f"only-fallback={sorted(fallback_skills - domain_skills)} only-domain={sorted(domain_skills - fallback_skills)}")

# --- D. pages & HTTP MCP -----------------------------------------------------
for page, marker in [("/", "Universal Systems Bridge"), ("/about", "PeepSick"),
                     ("/privacy", "KVKK"), ("/playground/intent-router", "intent-router")]:
    html = get(page)
    check(f"D page {page} 200 + marker", marker in html, html[:80])

disc = json.loads(get("/api/mcp"))
names = sorted(disc.get("tools", []))
check("D MCP discovery GET lists 4 tools",
      names == ["usb_audit_skill", "usb_get_skill", "usb_render_install", "usb_search"],
      str(names))

tl = post_json("/api/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
t_names = sorted(t["name"] for t in tl["result"]["tools"])
check("D MCP tools/list -> 4 tools", len(t_names) == 4, str(t_names))

sr = post_json("/api/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "usb_search", "arguments": {"query": "postgres", "limit": 5}}})
content = json.dumps(sr)
check("D MCP usb_search returns results", "postgres" in content.lower(), content[:120])

ri = post_json("/api/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "usb_render_install",
                                       "arguments": {"target": "claude", "slug": "intent-router"}}})
check("D MCP usb_render_install renders",
      "install" in json.dumps(ri).lower(), json.dumps(ri)[:120])

# unknown method and tool surface errors
err = post_json("/api/mcp", {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "usb_nope", "arguments": {}}})
check("D MCP unknown tool -> error", "error" in json.dumps(err).lower() or
      "not found" in json.dumps(err).lower(), json.dumps(err)[:160])

# --- summary -----------------------------------------------------------------
print()
print(f"passed={PASSES} failed={len(FAILURES)}")
if FAILURES:
    print("FAILURES:")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("ALL HTTP MATRIX TESTS PASSED")
