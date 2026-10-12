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

## Hızlı başlangıç (iki makine)

Her iki makinede de aynı gizli anahtar, karşı tarafın IP'si ve makineye özgü bir etiket gerekir. Etiket,
uzak oturumların `ListAgents` çıktısında nasıl görüneceğini belirler (`macB/Super Frontend`).

```bash
# bir kez, herhangi bir makinede: ortak gizli anahtar üret
openssl rand -hex 24
```

```bash
# Makine A (192.168.1.10)
curl -fsSL https://raw.githubusercontent.com/mehmetraufoguz/peerlink/main/bootstrap.sh \
  | bash -s -- --secret <ANAHTAR> --peer 192.168.1.20 --label macA
peerlink up
```

```bash
# Makine B (192.168.1.20)
curl -fsSL https://raw.githubusercontent.com/mehmetraufoguz/peerlink/main/bootstrap.sh \
  | bash -s -- --secret <ANAHTAR> --peer 192.168.1.10 --label macB
peerlink up
```

`bootstrap.sh` repoyu `~/.peerlink` altına klonlar (veya günceller) ve `install.sh`'i çalıştırır:
`~/.local/bin/peerlink` komutu, `~/.claude/skills/peerlink` skill symlink'i ve
`~/.config/peerlink/config.json`. Bağımlılık yok; pipx varsa onu kullanır, yoksa klona işaret eden bir
sarmalayıcı yazar. Python 3.9+ ve git yeter.

`peerlink up` ön planda çalışır; arka plan için `nohup peerlink up > ~/.peerlink.log 2>&1 &` ya da
[launchd/com.peerlink.plist.example](launchd/com.peerlink.plist.example). Birkaç saniye sonra `peerlink who`
eşi `UP` göstermeli.

### Karşı makinedeki Claude oturumunun yapması gereken bir şey yok

Relay çalıştığı sürece oradaki her oturum otomatik olarak bu makinede `macB/<ad>` olarak görünür;
oturumun yeniden başlatılması, bir ayar açılması veya skill kurulması gerekmez. Skill yalnızca mesajı
**gönderen** tarafın Claude'una yardım eder: uzak oturumları nasıl bulacağını ve relay'i nasıl teşhis
edeceğini bilir. Alıcı taraf mesajı yerel bir peer'dan gelmiş gibi alır.

Oturumlara ad vermek işi kolaylaştırır: `claude --name "API"` ya da oturum içinde `/rename API`. Adsız
oturumlar `session-<pid>` olarak listelenir.

### Elle kurulum

```bash
git clone https://github.com/mehmetraufoguz/peerlink.git && cd peerlink
./install.sh --secret <ANAHTAR> --peer 192.168.1.20 --label macA   # argümansız: config'e dokunmaz
```

Kurulum yapmadan da çalışır: `python3 -m peerlink <komut>` (repo kökünden).

## Kullanım

```bash
peerlink up                                      # relay'i başlat (ön planda); arka plan için nohup/launchd
peerlink who                                     # relay durumu, yerel oturumlar, proxy'ler, eşlerdeki oturumlar
peerlink who --host 192.168.1.20                 # yalnızca o makinenin oturumları
peerlink send macB/"Super Frontend" "merhaba"    # proxy'siz tek seferlik gönderim (cevap dönmez)
peerlink doctor                                  # python, config, relay, eşler (401 / zaman aşımı / reddedildi), ölü proxy'ler
peerlink clean                                   # ölü proxy kayıtlarını temizle
```

### `peers` nasıl çalışır

`--peer HOST[:PORT]` tekrarlanabilir; config'te `peers` bir listedir. Her giriş **tek bir makinedir**, aralık ya
da CIDR yoktur ve otomatik keşif yapılmaz. Relay her 5 saniyede listedeki her eşe `/sessions` sorar ve oradaki
oturumları aynalar. Üç makine için her makinede diğer ikisi listelenir. Port verilmezse 7437. Eşin kendi etiketi
(`label`) proxy adlarının önekidir ve makine başına benzersiz olmalıdır; `peerlink doctor` çakışmayı yakalar.

Relay çalıştıktan birkaç saniye sonra bir Claude Code oturumunda `ListAgents` çağır; `macB/...` satırları
belirir. `SendMessage` ile adı aynen kullanarak yaz.

Tek makinede deneme için relay kendi kendine eş olabilir:

```bash
peerlink up --secret test --peer 127.0.0.1:7437 --bind 127.0.0.1 --label remote
```

## Claude Code skill'i

[skills/peerlink/SKILL.md](skills/peerlink/SKILL.md), bir Claude Code oturumuna uzak oturumları nasıl
bulacağını, mesaj atacağını ve relay'i nasıl kuracağını/teşhis edeceğini öğretir. Skill kendi kendine yeter:
`scripts/ensure.sh` peerlink'in kurulu olup olmadığını denetler, değilse `bootstrap.sh` ile kurar, sonra
`peerlink doctor` çalıştırır. Yani skill'i tek başına kuran bir oturum da işi sıfırdan tamamlayabilir.

```bash
npx skills add mehmetraufoguz/peerlink -g          # yalnızca skill (kod yok; ilk kullanımda ensure.sh kurar)
./install.sh                                        # repo'dan: ~/.claude/skills/peerlink -> bu klasöre symlink
```

[skills.sh](https://skills.sh) dizini ayrı bir yayınlama adımı istemez; `npx skills add <owner/repo>`
çalıştırıldıkça anonim telemetriyle otomatik listelenir. Skill, [Agent Skills](https://agentskills.io)
spesifikasyonuna uyar (`skills/<ad>/SKILL.md`, `name` klasör adıyla aynı).

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
