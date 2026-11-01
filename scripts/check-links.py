#!/usr/bin/env python3
"""Check the live site's own references resolve: anchors, assets, API paths.

This is the check that would have caught the "Try It Live" button bug:
verify-readme.sh tests CLI commands and check-encoding.py tests the catalog
API, but neither one ever visited the website's own pages and followed its
navigation — this script does exactly that.

Reality of the landing page: it is a single-file inlined-HTML app — zero
server-rendered <a href>, and no <script src>/<link href> referencing our
own assets (preloads are wired at runtime by inline JS; the only static
<script src> is Cloudflare's beacon). A 2025-era version of this script
reported "0 internal links found -> OK" because of exactly that, so it now
ALSO extracts the internal API path literals the page bundle really calls
(`/api/health`, `/api/skills`, `/api/version`, ...) out of the SSR HTML and
probes them. Between the three sources — tag anchors, asset tags, and
bundle literals — a 404-class regression on our own URLs fails here, and a
page whose fetch came back bot-blocked or structurally empty fails instead
of silently passing on zero references.

Fetching uses curl's default UA on every platform (Linux CI, WSL, Windows)
so the probe goes through the site's edge rules the same way everywhere.

External references (github.com, npmjs.com, fonts, mailto:, ...) are
skipped on purpose — this is about catching our own broken references, not
policing the internet.
"""
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

# Force UTF-8 stdout regardless of the host console's default codepage —
# see scripts/check-encoding.py for why this matters on Windows.
sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://usbplus.peepsicklabs.com"
# Just the landing — it is the only HTML page left; every other reachable
# surface (footer links included) is discovered from its references.
ENTRY_PAGES = ["/"]

# Internal API routes called from the page's own bundle (inline script
# text). Kept tight to avoid noise: our own single internal API prefix as
# it appears quoted in JS.
API_LITERAL_RE = re.compile(r"""['"`](/api/[a-zA-Z0-9_?=&\-./]+)['"`]""")

# Which URL-bearing attribute each tag may contribute.
REFERENCE_ATTRS = {"a": "href", "script": "src", "img": "src", "link": "href"}


class ReferenceExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = REFERENCE_ATTRS.get(tag)
        if not attr:
            return
        for name, value in attrs:
            if name == attr and value:
                self.refs.append(value)


def curl_code(url: str, head: bool = False) -> int:
    """Status code via curl; connection errors map to a self-made 599."""
    cmd = ["curl", "-sS", "-o", os.devnull, "-w", "%{http_code}", "--max-time", "30"]
    if head:
        cmd.insert(1, "-I")
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return 599
    code = proc.stdout.strip()
    return int(code) if code.isdigit() else 599


def curl_body(url: str) -> str:
    try:
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", "90", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=100,
        )
    except subprocess.TimeoutExpired:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def is_internal(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        # mailto:, tel:, javascript:, data:, blob: are never fetched
        return False
    if parsed.path.startswith("/cdn-cgi/"):
        # Cloudflare rewrites mailto: links into /cdn-cgi/l/email-protection#...
        # server-side (its bot-scraping countermeasure) and decodes them back
        # client-side via injected JS. A crawler GET-ing that path directly
        # 404s by design — it was never meant to be fetched, only decoded.
        return False
    if not parsed.netloc:
        return True
    return parsed.netloc == urlparse(BASE_URL).netloc


def clean_literal(path: str) -> str:
    # A bundle literal can end in placeholder punctuation (`?`, `&`, `=`)
    # that would turn the probe into an out-of-spec request; probe the
    # bare route instead.
    return path.rstrip("?&=")


def main() -> int:
    # (kind, url), deduped. kind: "link" = <a href>, "asset" = script/img/link
    # tags, "api" = internal API path literal extracted from the page bundle.
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, url: str) -> None:
        merged = urljoin(BASE_URL, url).split("#", 1)[0]
        if merged and is_internal(merged) and merged not in seen:
            seen.add(merged)
            refs.append((kind, merged))

    for page in ENTRY_PAGES:
        url = urljoin(BASE_URL, page)
        html = curl_body(url)
        if not html:
            print(f"❌ FAILED — could not even fetch the entry page: {url}")
            return 1

        parser = ReferenceExtractor()
        parser.feed(html)
        for ref in parser.refs:
            add("link", ref)

        for literal in API_LITERAL_RE.findall(html):
            clean = clean_literal(literal)
            if clean:
                add("api", clean)

    anchors = sum(1 for kind, _ in refs if kind == "link")
    assets = sum(1 for kind, _ in refs if kind == "asset")
    apis = sum(1 for kind, _ in refs if kind == "api")

    print(
        f"Found {len(refs)} unique internal references "
        f"({anchors} anchors, {assets} asset tags, {apis} bundle API paths) "
        f"across {len(ENTRY_PAGES)} entry pages."
    )

    if not refs:
        print(
            "❌ FAILED — the SSR HTML yielded no anchors, no asset tags and no "
            "API path literals.\nA landing page always calls at least one API at "
            "runtime, so this means the fetch came back bot-blocked or the page\n"
            "structure changed beyond what this check can reason about."
        )
        return 1
    if anchors == 0:
        print(
            "Note: 0 <a href> anchors in the SSR HTML — navigation and "
            "app routes are wired client-side; this check probes the "
            "asset tags and/or the API paths the bundle references."
        )

    broken: list[tuple[str, int, str]] = []
    for kind, url in refs:
        status = curl_code(url, head=(kind == "asset"))
        marker = "✅" if status < 400 else "❌"
        print(f"  {marker} {status} {url} [{kind}]")
        if status >= 400:
            broken.append((url, status, kind))

    if broken:
        print(f"\n❌ FAILED — {len(broken)} broken internal reference(s):")
        for url, status, _kind in broken:
            print(f"  {url} -> {status}")
        return 1

    print(f"\n✅ OK — all {len(refs)} internal references resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
