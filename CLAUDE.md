# peerlink

Claude Code oturumlarını LAN üzerinden mesajlaştıran relay. Tek dosya mantık: `peerlink/relay.py`.
Bağımlılık yok, Python 3.9+. Çalıştır: `python3 -m peerlink <komut>` veya kurulu `peerlink`.

- Protokol belgelenmemiş; `docs/PROTOCOL.md` gözlemle çıkarılmış tel formatını anlatır. Claude Code sürümü
  değişince `scripts/probe_listener.py` ile yeniden doğrula.
- `relay.py` içindeki `proxy` alt komutu relay tarafından `sys.executable <dosya> proxy ...` ile spawn edilir;
  modül kendi içinde kalmalı (relative import ekleme).
- Testler gerçek oturum gerektirmez: `scripts/roundtrip_test.py` iki sahte oturum (`probe_listener.py`) ve
  loopback relay ile gidiş-dönüş doğrular. Çalıştırma adımları `docs/TESTING.md`.
- Skill: `skills/peerlink/SKILL.md`; `install.sh` bunu `~/.claude/skills/` altına symlink'ler. Skill `npx skills add` ile
  tek başına da kurulabilir; `skills/peerlink/scripts/` içindeki betikler repo'nun geri kalanına bağımlı olmamalı
  (kod yoksa `bootstrap.sh` URL'sinden kurarlar).
- Gizli anahtar hiçbir zaman loga veya çıktıya yazılmaz.
- Commit mesajları Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`; kırıcı değişiklikte
  `!` ve `BREAKING CHANGE:` gövdesi. Özet satırı İngilizce, emirsiz, 72 karakter altı.
