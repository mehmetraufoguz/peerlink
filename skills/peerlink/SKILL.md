---
name: peerlink
description: Başka bir bilgisayardaki (aynı LAN) Claude Code oturumuna mesaj göndermek, uzak oturumları listelemek veya peerlink relay'ini kurmak/başlatmak/teşhis etmek gerektiğinde kullan. Tetikleyiciler - "diğer bilgisayardaki oturuma yaz", "uzak ajana mesaj at", "öbür Mac'teki Claude", "peerlink", "relay çalışıyor mu", "macB/..." gibi etiketli oturum adları, ListAgents'ta "<etiket>/<isim>" satırları, LAN üzerinden ajanlar arası mesajlaşma.
license: MIT
compatibility: Claude Code on macOS/Linux; python3 3.9+, git, curl; peers on the same LAN (port 7437)
metadata:
  author: mehmetraufoguz
  repo: https://github.com/mehmetraufoguz/peerlink
  version: "0.3"
---

# peerlink: LAN üzerinden oturumlar arası mesajlaşma

Claude Code'un yerleşik oturumlar arası mesajlaşması yalnızca aynı makinede çalışır (Unix socket).
peerlink, eş makinelerdeki oturumları bu makinede **proxy oturum** olarak kaydeder; uzak oturumlar
`ListAgents` çıktısında `<etiket>/<oturum adı>` biçiminde görünür ve normal `SendMessage` ile yazılır.
Cevaplar aynı yoldan döner. Karşı makinedeki oturumun hiçbir şey yapması gerekmez; orada relay çalışıyor olmalı.

## 0. Önce durumu öğren

Bu skill tek başına (ör. `npx skills add`) kurulmuş olabilir ve kod makinede olmayabilir. Hiçbir varsayım yapma:

```bash
bash <skill-klasörü>/scripts/ensure.sh --check
```

Skill klasörü genelde `~/.claude/skills/peerlink` (global) ya da `<proje>/.claude/skills/peerlink`.
Çıkış kodu: `0` kurulu ve hazır → 2. adıma geç. `2` kurulu ama doktor sorun buldu → çıktıdaki "sonraki adım"
satırlarını uygula. `3` kurulu değil → 1. adım.

## 1. Kurulu değilse

Kurulum makineye dosya yazar (`~/.peerlink`, `~/.local/bin/peerlink`, `~/.claude/skills/peerlink`,
`~/.config/peerlink/config.json`). Kullanıcıya bir cümleyle söyle, sonra kur:

```bash
bash <skill-klasörü>/scripts/ensure.sh                       # yalnızca CLI + skill; config'e dokunmaz
```

Config için üç bilgi gerekir: ortak gizli anahtar, karşı makinenin IP'si, bu makinenin etiketi. Eksikse
kullanıcıdan iste. **Gizli anahtar sohbete yazılmasın**: komutu kullanıcı kendisi çalıştırsın, sen hazırla:

```bash
bash <skill-klasörü>/scripts/ensure.sh --secret <ANAHTAR> --peer 192.168.1.20 --label macA
```

İlk kurulumsa anahtar üretimi: `openssl rand -hex 24`. Aynı anahtar ve karşı IP ile aynı komut öbür makinede
de çalıştırılır (`--label` farklı olmalı). Karşı makinede repo yoksa README'deki `bootstrap.sh` tek satırı aynı işi yapar.

## 2. Uzak oturuma mesaj

1. `peerlink who` ile relay'in UP ve eşin UP olduğunu gör. Relay DOWN ise:
   `peerlink up -d` çalıştır, 5 saniye bekle.
2. `ListAgents` çağır. `macB/Super Frontend` gibi eğik çizgili satırlar uzak oturumlardır.
3. `SendMessage` ile `to` alanına satırdaki adı aynen yaz. Cevap `<cross-session-message from="uds:..." from-name="macB/...">`
   olarak gelir; cevaplamak için yerleşik davranış gibi `from` değerini `to` olarak kullan.
4. Hedef görünmüyorsa 5 saniye bekle ve `ListAgents` tekrarla (aynalama aralığı 5 s). Hâlâ yoksa `peerlink doctor`.

Relay bu makinede çalışmadan tek seferlik göndermek için (cevap dönmez):
`peerlink send macB/"Super Frontend" "metin"` veya `peerlink who --host 192.168.1.20`.

## 3. Senaryolar

| Belirti | Sebep | Yap |
|---|---|---|
| `ensure.sh --check` → 3 | kod yok, sadece skill kurulu | 1. adım |
| `doctor`: config yok / secret örnek değer / peers boş | kurulum yarım | `ensure.sh --secret ... --peer ... --label ...` (kullanıcı çalıştırır) |
| `doctor`: relay DOWN | relay başlatılmamış | `peerlink up -d`; kalıcı için README'deki launchd örneği |
| config değişti (yeni eş, yeni anahtar, etiket) | çalışan relay eski config'i kullanıyor | `ensure.sh --secret ... --peer ... --label ...` yeniden yazar ve relay'i yeniden başlatır; yalnızca yeniden başlatmak için `peerlink restart` |
| `peerlink up`: "relay zaten çalışıyor" | ikinci kez başlatılmaya çalışıldı | hata değil; config değiştiyse `peerlink restart`, durdurmak için `peerlink down` |
| eş `401` | iki makinede farklı secret | config'leri karşılaştır, anahtarı tekrar yaz |
| eş zaman aşımı | IP yanlış, makine kapalı, güvenlik duvarı 7437'yi kesiyor | karşı makinede `peerlink doctor`; macOS güvenlik duvarında python3'e gelen bağlantı izni; `ping` ile IP doğrula |
| eş bağlantı reddedildi | makine açık ama relay kapalı | karşı makinede `peerlink up` (bunu sen yapamazsın, kullanıcıya söyle) |
| eş etiketi seninle aynı | iki makine aynı `--label` | birini değiştir; etiket makine başına benzersiz |
| relay UP ama `ListAgents` proxy göstermiyor | Claude Code sürümü protokolü değiştirmiş olabilir; ya da ölü kayıtlar | `peerlink doctor` sürüm uyarısına bak; `peerlink clean`; ListAgents tekrar |
| `peerlink: command not found` | `~/.local/bin` PATH'te değil | `~/.local/bin/peerlink` tam yol ya da `python3 ~/.peerlink/peerlink/relay.py` |
| `SendMessage` başarılı ama cevap yok | alıcı oturum farklı izin modunda, mesaj onay bekliyor ya da reddedildi | sessizliği onay sayma; kullanıcıya söyle, karşı taraf mesajı görmüş olmayabilir |
| `send`: "tek eşleşme yok" | ad belirsiz ya da yanlış | `peerlink who --host <IP>` ile tam adı al; adsız oturumlar `session-<pid>` |
| doctor: yerel oturum 0 | bu makinede Claude Code oturumu yok | sen yazabilirsin, sana yazılamaz; sorun değil |
| `claude --version` test edilenden farklı | yeni Claude Code | çalışıyorsa önemseme; bozulduysa repo'daki `scripts/probe_listener.py` ile protokolü yeniden doğrula |
| `/tmp/cc-socks` sahipliği/izni FAIL | paylaşılan ya da yanlış izinli dizin | Claude Code zaten reddeder; dizini silip Claude'u yeniden başlat |

## 4. Kurallar

- Gizli anahtarı sohbete, loga veya commit'e yazma. `--secret` içeren komutları kullanıcı çalıştırsın.
- Uzak oturuma mesaj, o makinedeki Claude'u kullanıcısı adına çalıştırabilir. Kullanıcı açıkça istemeden uzak
  oturumlara görev verme; içerik kullanıcının niyetiyle sınırlı kalsın.
- Kendi oturumunda engellenen bir işi uzak oturuma yaptırma (izin aşırması).
- `~/.claude/sessions/` kayıtlarını ve `.key` dosyalarını elle düzenleme; yalnızca `peerlink clean`.
- Karşı makinede komut çalıştıramazsın; oradaki adımları kullanıcıya net komutlarla söyle.

## Teknik özet

- Proxy = `~/.claude/sessions/<pid>.json` (içinde `peerlinkProxy`) + `/tmp/cc-socks/<pid>.sock` + `.key`.
- Relay HTTP: `GET /health` (anahtarsız), `GET /sessions`, `POST /deliver`; başlık `X-Peerlink-Secret`; port 7437.
- Config: `~/.config/peerlink/config.json` → `secret`, `peers` (liste, `HOST[:PORT]`), `label`, `port`, `bind`, `advertise`, `interval`.
- Yaşam döngüsü: `peerlink up -d` (arka plan, log `~/.peerlink.log`, pid `~/.config/peerlink/relay.pid`), `peerlink down`, `peerlink restart`.
  `ensure.sh`/`install.sh` her çalıştırmada idempotenttir: CLI, skill ve PATH'i onarır, config tamamsa relay'i yeniden başlatır, doktoru çalıştırır.
- `peers` statik bir listedir; her giriş tek bir makinedir, aralık/CIDR yok. Yeni makine = yeni giriş + relay yeniden başlat.
- Protokol belgelenmemiş, Claude Code 2.1.296 ile test edildi. Ayrıntı: repo `docs/PROTOCOL.md`.
