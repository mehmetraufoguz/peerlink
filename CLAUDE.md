# agent-relay-peer

Claude Code oturumlarını LAN üzerinden mesajlaştıran relay. Tek dosya mantık: `agent_relay_peer/relay.py`.
Bağımlılık yok, Python 3.9+. Çalıştır: `python3 -m agent_relay_peer <komut>` veya kurulu `agent-relay`.

- Protokol belgelenmemiş; `docs/PROTOCOL.md` gözlemle çıkarılmış tel formatını anlatır. Claude Code sürümü
  değişince `scripts/probe_listener.py` ile yeniden doğrula.
- `relay.py` içindeki `proxy` alt komutu relay tarafından `sys.executable <dosya> proxy ...` ile spawn edilir;
  modül kendi içinde kalmalı (relative import ekleme).
- Testler gerçek oturum gerektirmez: `scripts/roundtrip_test.py` iki sahte oturum (`probe_listener.py`) ve
  loopback relay ile gidiş-dönüş doğrular. Çalıştırma adımları `docs/TESTING.md`.
- Skill: `skills/agent-relay-peer/SKILL.md`; `install.sh` bunu `~/.claude/skills/` altına symlink'ler.
- Gizli anahtar hiçbir zaman loga veya çıktıya yazılmaz.
