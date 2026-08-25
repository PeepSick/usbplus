# DEVAM.md — kaldığımız yer (checkpoint)

_Güncel gerçek: origin/main = `ba56fde` · lokal = senkron · P103 pushlandı ·
UX turu landed · E2E corpus CI'de · JSON/artifact sözleşmesi kilitli._

## Durum özeti

Faz 5 ve UX turu KAPANDI. Büyük resim:

```
USB v2 Trust Layer
├─ Repo discovery
├─ Skills (SKILL.md kartları + bundle script kanıtı)
├─ MCP
│  ├─ config (.mcp.json / .cursor / .vscode)
│  ├─ system scope (--system user-scope dosyaları)
│  ├─ declared tools (entry tools + Cursor mcpAllowlist)
│  ├─ permissions (Claude Code settings → SADECE authority)
│  ├─ authority scope (env/transports/mounts/permissions)
│  └─ excessive-authority (P103, advisory)
├─ Shared contract artefacts (usb-contract-artifacts/v1)
├─ Independent reducer (conformance ile byte/semantic kilitli)
└─ E2E reference corpus (acme-agent, CI adımı)
```

## Sıradaki işler

1. **Stage 3 tool graph — tasarım tartışmasıyla başla.** MCP tool
   graph'ını elde etmenin kaynaklarını belirle: declared protocol
   metadata, implementation source veya açıkça işaretlenmiş runtime
   observation. Statik deterministik analiz ile ağ/işlem gerektiren
   observation birbirine karıştırılmayacak.
2. **Uzak ufuk:** implementation source ve runtime observation. Bunlar
   Stage 3 tasarımından sonra, önceki katmanların verdiği kanıta göre
   ayrı derinlik seviyeleri olarak ele alınacak.
3. **Ufak:** `--system` taramasında user-scope MCP birimleri için
   kök-etiketli locator'ları gözden geçir.

## Stage 3 kapı sorusu (koda başlamadan önce cevaplanacak)

> MCP server'ın tool graph'ı, server'ı çalıştırmadan ve ağa bağlanmadan
> gerçekten hangi statik kaynaklardan güvenilir biçimde çıkarılabilir?

Tasarım taslağı: `docs/tool-graph-sources.md` (bu sorunun analizi;
kabul/red kararları conformance-first kültürüne uygun şekilde belgede
yaşayacak).

## Kilitli kararlar (tarihçe, kısa)

- v1 config layer: keşif sabit relpath'ler; claim = sunucu adı; env
  adları davranış değil authority; bozuk config S101 → unverifiable.
- v2 user-scope: --system altında ~/.claude.json (state-dump toleranslı),
  Cursor global, Windsurf, Claude Desktop; routing guard markdown hattını
  korur.
- v2.5 declared tools: entry `tools` + Cursor mcpAllowlist; uydurma yok
  (yoksa tools_declared=false); okunamayan liste S102 → unverifiable.
- v3 Claude Code izinleri: allow/deny/ask verbatim authority_scope'a;
  declaration'a sızmaz (test kilidi); bozuk settings standalone S101.
- v4 P103 excessive-authority: hep low/advisory; kelime-dağarcığı testi;
  transport hariç, operatör izinleri yargılanmaz; reducer birebir ayna.
- E2E: acme-agent ağacı + 3 normalize golden + reducer + CI adımı;
  normalize JSON escape'lere dokunmaz, golden okuma evrensel yeni satır.

## Ortam notları

- Bash: `$env:ProgramFiles\Git\bin\bash.exe` (PS içinden `& $bash -c '...'`)
- PS quoting tuzakları: parantezli commit mesajı → `-F dosya`; çok satırlı
  bash/here-doc → dosyaya yazıp çalıştır; `$?`/`$(...)` içeren komutlar
  tek tırnakta bile bazen bozuluyor → ayrı ayrı çalıştır.
- python3 Git Bash içinde çalışıyor (Store kurulumu).
