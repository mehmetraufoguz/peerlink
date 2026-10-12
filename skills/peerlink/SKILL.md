---
name: peerlink
description: Başka bir bilgisayardaki (aynı LAN) Claude Code oturumuna mesaj göndermek, uzak oturumları listelemek veya LAN relay'ini (peerlink) başlatmak/teşhis etmek gerektiğinde kullan. Tetikleyiciler - "diğer bilgisayardaki oturuma yaz", "uzak ajana mesaj at", "peerlink", "relay çalışıyor mu", "macB/..." gibi etiketli oturum adları, ListAgents'ta "<etiket>/<isim>" satırları.
---

# peerlink: LAN üzerinden oturumlar arası mesajlaşma

Claude Code'un yerleşik oturumlar arası mesajlaşması yalnızca aynı makinede çalışır (Unix socket).
`peerlink`, eş makinelerdeki oturumları bu makinede **proxy oturum** olarak kaydeder; böylece uzak
oturumlar `ListAgents` çıktısında `<etiket>/<oturum adı>` biçiminde görünür ve normal `SendMessage`
ile yazılabilir. Cevaplar da aynı yoldan geri döner.

## Hızlı yol: uzak oturuma mesaj

1. `ListAgents` çağır. `macB/Super Frontend` gibi eğik çizgili satırlar uzak oturumlardır.
2. Hedef görünüyorsa doğrudan `SendMessage` kullan, `to` olarak satırdaki adı aynen ver.
3. Gelen cevap `<cross-session-message from="uds:/tmp/cc-socks/<pid>.sock" from-name="macB/...">` olarak
   gelir; cevaplamak için `from` değerini `to` olarak kullan (yerleşik davranış).

Hedef listede yoksa relay'in durumuna bak:

```bash
peerlink who
```

## Durum yorumlama

- `local relay: DOWN` → relay çalışmıyor. Yapılandırma varsa `peerlink up` ile başlat
  (arka planda: `nohup peerlink up > ~/.peerlink.log 2>&1 &`). Yapılandırma yoksa kullanıcıdan
  eş IP'si ve paylaşılan gizli anahtarı iste; `~/.config/peerlink/config.json` örneği README'de.
- `peers: ... DOWN` → karşı makinede relay kapalı, IP yanlış, port kapalı veya gizli anahtar farklı (401).
  Bunu kullanıcıya söyle; karşı makineye kendin erişemezsin.
- `remote proxies (0)` ama eş UP → birkaç saniye bekle (reconcile aralığı varsayılan 5 s), sonra `ListAgents` tekrarla.
- Proxy'ler ölü (`peerlink who` listelemiyor ama `ListAgents` gösteriyor) → `peerlink clean`.

## Proxy'siz tek seferlik gönderim

Relay bu makinede çalışmıyor ama karşı makinede çalışıyorsa:

```bash
peerlink who --host 192.168.1.20                      # yalnızca o makinenin oturumları
peerlink send macB/"Super Frontend" "mesaj metni"     # etiket config'teki eşlerden çözülür
```

Bu yolda cevap alınamaz (`from` adresi cevap verilemez bir yola işaret eder); cevap gerekiyorsa relay'i başlat.

## Kurallar

- Gizli anahtarı sohbete yazma, loglara basma. `--secret` yerine config dosyasını veya
  `PEERLINK_SECRET` ortam değişkenini tercih et.
- Uzak oturuma mesaj göndermek, o oturumun kullanıcısı adına iş başlatabilir. Kullanıcı açıkça istemeden
  uzak oturumlara görev verme; mesaj içeriğini kullanıcının niyetine sadık tut.
- Kendi oturumunda engellenen bir işi uzak oturuma yaptırma (izin aşırması).
- `~/.claude/sessions/` altındaki kayıtları ve `.key` dosyalarını elle düzenleme; yalnızca `peerlink clean` kullan.

## Teknik özet (hata ayıklama için)

- Proxy = registry kaydı `~/.claude/sessions/<pid>.json` (içinde `peerlinkProxy` alanı) + `/tmp/cc-socks/<pid>.sock` + `.key`.
- Relay HTTP: `GET /health` (anahtarsız), `GET /sessions`, `POST /deliver`; başlık `X-Peerlink-Secret`. Varsayılan port 7437.
- `procStart` alanı `TZ=UTC ps -o lstart=` çıktısıyla eşleşmeli; eşleşmezse kayıt stale sayılır ve proxy listede görünmez.
- Protokol belgelenmemiş ve Claude Code 2.1.296 ile test edildi; `ListAgents` proxy'leri göstermiyorsa önce Claude Code sürümünü kontrol et.
