# mcprobe Calibration — Run #2 (2026-08-27)

## Config

9 hedef, `--timeout 20`, config: `%TEMP%/opencode/mcprobe-calib2/.mcp.json`

## Sonuçlar

| # | Hedef | Transport | Durum | Tool | Not |
|---|---|---|---|---|---|
| 1 | deepwiki-sse | SSE | protocol_error: HTTP 405 | 0 | Legacy SSE desteklenmiyor (önceden de belgeli) |
| 2 | semgrep | HTTP | protocol_error: HTTP 404 | 0 | URL geçersiz veya endpoint taşınmış |
| 3 | chainflip | HTTP | **complete** | 6 | Broker as a Service — crypto swap, auth yok ✓ |
| 4 | deepwiki-http | HTTP | **complete** | 3 | v2.14.3 — ask_question, read_wiki_* (önceden de birebir ✓) |
| 5 | 402bot | HTTP | protocol_error: HTTP 405 | 0 | Endpoint desteklemiyor |
| 6 | roundtable | HTTP | **complete** | 13 | Multi-model AI brainstorming, auth yok ✓ |
| 7 | bgpt | HTTP | auth_required: HTTP 401 | 0 | Auth gerektiriyor (beklenti: authsiz) |
| 8 | web-reader | HTTP | **complete** | 4 | vanshul-web-reader v1.0.0 (önceden de birebir ✓) |
| 9 | spacemolt | HTTP | **complete** | 80+ | MMO oyun, auth yok (kayıt ile) ✓ |

## Özet

- **Başarılı:** 5/9 (chainflip, deepwiki-http, roundtable, web-reader, spacemolt)
- **Auth gerekli:** 1/9 (bgpt → HTTP 401)
- **Protocol error:** 3/9 (deepwiki-sse 405, semgrep 404, 402bot 405)

## Declare-vs-Runtime Uyuşma

| Sunucu | Declared tool | Runtime tool | Uyuşma |
|---|---|---|---|
| deepwiki-http | 3 (static config'den) | 3 (ask_question, read_wiki_contents, read_wiki_structure) | **birebir** ✓ |
| web-reader | 4 (static config'den) | 4 (extract_links, fetch_markdown, fetch_metadata, search_page) | **birebir** ✓ |

Yeni sunucular (chainflip, roundtable, spacemolt) için static config olmadığından karşılaştırma yapılamıyor — bunlar远程-only, in-tree kaynak yok.

## P104 Durumu

**Toplam örnek:** 4/4 uyuşma (deepwiki-http 2 tur, web-reader 2 tur)
**Hâlâ report-only:** Evet. Mismatch henüz görülmedi.
**Sonraki adım:** Daha fazla OAuth-olmayan sunucu test et; auth_required ile auth_required olmayan ayrımı netleştir.

## Yeni Bulgu

- **Chainflip Broker:** İlk kez test edilen no-auth remote MCP — crypto swap tool'ları, `Mcp-Session-Id` döndürmüyor (stateless).
- **Roundtable:** İlk kez test edilen no-auth remote MCP — 13 tool (multi-model brainstorming), stateless.
- **Spacemolt:** 80+ tool ile en büyük tool set — MMO oyun, stateless API.
- **BGPT:** Auth gerektiriyor (beklenti: authsiz idi). auth_required ayrımı için iyi veri noktası.
