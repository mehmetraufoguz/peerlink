# peerlink

Claude Code oturumlarını **aynı LAN'daki başka bilgisayarlarla** mesajlaştırır. Uzak makinedeki
oturumlar bu makinede `ListAgents` çıktısında `<etiket>/<oturum adı>` olarak görünür ve normal
`SendMessage` ile yazılır; cevaplar da geri döner. Anthropic sunucularından geçmez, internet gerekmez.

> Claude Code'un yerleşik oturumlar arası mesajlaşması yalnızca aynı makinede çalışır (Unix domain socket).
> Resmi "başka makine" yolu Remote Control'dür ve Anthropic sunucuları üzerinden geçer. Bu proje, yalnızca
> LAN içinde kalan bir alternatiftir. Belgelenmemiş bir yerel protokole dayanır; bkz. [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Nasıl çalışır

```
Makine A                                   Makine B
oturum X ──SendMessage "macB/Y"──▶ proxy(macB/Y) ──HTTP──▶ relay B ──unix socket──▶ oturum Y
oturum X ◀──unix socket── relay A ◀──HTTP── proxy(macA/X) ◀──SendMessage "macA/X"── oturum Y
```

Her makinede bir `peerlink up` çalışır. Relay, eş makinelerin oturum listesini çeker ve her uzak
oturum için yerel bir **proxy oturum** süreci başlatır. Proxy, Claude Code'un oturum kayıt defterine
(`~/.claude/sessions/`) gerçek bir oturum gibi kaydolur ve `/tmp/cc-socks/<pid>.sock` üzerinde dinler.
Proxy'ye yazılan çerçeve HTTP ile karşı relay'e gider; karşı relay onu hedef oturumun socket'ine
native protokolle teslim eder. Teslimatta `from` alanı, gönderenin karşı taraftaki proxy'sine yeniden
yazılır; cevap bu sayede aynı yoldan döner. Proxy'ler hedefin idle/busy durumunu da aynalar.

## Kurulum

```bash
git clone <repo> && cd peerlink
./install.sh          # peerlink CLI (pipx/pip --user) + ~/.claude/skills/peerlink symlink + config taslağı
```

Kurulum yapmadan da çalışır: `python3 -m peerlink <komut>`.

`~/.config/peerlink/config.json` dosyasını doldur (her makinede aynı `secret`, karşı makinenin IP'si `peers` içinde):

```json
{ "secret": "uzun-rastgele-anahtar", "peers": ["192.168.1.20"], "label": "macA", "port": 7437 }
```

## Kullanım

```bash
peerlink up                                      # relay'i başlat (ön planda); arka plan için nohup/launchd
peerlink who                                     # relay durumu, yerel oturumlar, proxy'ler, eşlerdeki oturumlar
peerlink who --host 192.168.1.20                 # yalnızca o makinenin oturumları
peerlink send macB/"Super Frontend" "merhaba"    # proxy'siz tek seferlik gönderim (cevap dönmez)
peerlink clean                                   # ölü proxy kayıtlarını temizle
```

Relay çalıştıktan birkaç saniye sonra bir Claude Code oturumunda `ListAgents` çağır; `macB/...` satırları
belirir. `SendMessage` ile adı aynen kullanarak yaz. Oturum açılışında otomatik başlatmak için
[launchd/com.peerlink.plist.example](launchd/com.peerlink.plist.example).

Tek makinede deneme için relay kendi kendine eş olabilir:

```bash
peerlink up --secret test --peer 127.0.0.1:7437 --bind 127.0.0.1 --label remote
```

## Claude Code skill'i

[skills/peerlink/SKILL.md](skills/peerlink/SKILL.md), bir Claude Code oturumuna uzak
oturumları nasıl bulacağını, mesaj atacağını ve relay'i nasıl teşhis edeceğini öğretir. `install.sh`
bunu `~/.claude/skills/peerlink` altına symlink'ler; proje bazlı istiyorsan `.claude/skills/`
altına kopyala.

## Güvenlik

- HTTP düz metindir. Yalnızca güvendiğin ağda kullan; gerekirse önüne TLS reverse proxy koy.
- Tüm istekler paylaşılan gizli anahtarla (`X-Peerlink-Secret`) doğrulanır. `/health` anahtarsızdır ve yalnızca etiket/sürüm döner.
- Uzak oturuma mesaj göndermek, o makinedeki Claude'u kullanıcısı adına çalıştırabilir. Claude Code'un
  `crossSessionInbound` ayarı (`accept`/`hold`/`refuse`) bu mesajlar için de geçerlidir; relay'den gelenler
  yerel bir peer'dan gelmiş gibi görünür.
- Proxy'ler kendi rastgele token'larıyla kaydolur; uzak oturumların gerçek token'ları ağa çıkmaz.

## Sınırlar

- Claude Code 2.1.296 ile test edildi. Kayıt defteri biçimi veya socket protokolü değişirse bozulabilir;
  `scripts/probe_listener.py` ile yeniden doğrulanır.
- Eş keşfi statik (`peers` listesi). mDNS yok.
- `notify_when_idle` gibi makine-içi özellikler proxy üzerinden çalışmaz.
- macOS'ta geliştirildi; Linux için `pidDomain` ve socket dizini (`/run/user/<uid>/cc-socks`) kontrol edilmeli.

## Test

Gerçek oturum gerektirmez. Bkz. [docs/TESTING.md](docs/TESTING.md).

## Lisans

MIT
