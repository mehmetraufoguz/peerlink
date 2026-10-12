# Test

Gerçek Claude oturumu gerekmez. İki sahte oturum ve loopback relay ile gidiş-dönüş doğrulanır.
Tüm komutlar repo kökünden.

```bash
# 1) relay, kendi kendine eş (loopback)
python3 -m peerlink up --secret test --peer 127.0.0.1:7437 --bind 127.0.0.1 --label remote --interval 2 &

# 2) iki sahte oturum (gelen her çerçeveyi scripts/probe.log'a yazar)
python3 scripts/probe_listener.py --name LAN-PROBE &
python3 scripts/probe_listener.py --name LAN-PROBE-2 &
sleep 4

# 3) gidiş-dönüş: LAN-PROBE -> remote/LAN-PROBE-2 (proxy) -> relay -> LAN-PROBE-2, sonra cevap
python3 scripts/roundtrip_test.py

# 4) temizlik
kill %1 %2 %3
python3 -m peerlink clean
```

Beklenen çıktı:

```
LAN-PROBE-2 gördüğü cevap adresi: /tmp/cc-socks/<pid>.sock -> proxy adı: remote/LAN-PROBE
LAN-PROBE aldığı cevabın from adresi: /tmp/cc-socks/<pid>.sock -> proxy adı: remote/LAN-PROBE-2
```

Gerçek oturumla doğrulama: relay çalışırken bir Claude Code oturumunda `ListAgents` çağır,
`remote/LAN-PROBE` satırına `SendMessage` ile yaz, `scripts/probe.log` içinde çerçeveyi gör.

## socat deneyi

`scripts/socat_peer.py`, uzak socket'i TCP ile tünelleyip sahte peer olarak kaydeder. Tek yönlüdür
(cevap yolu yok) ve uzak token'ın elle kopyalanmasını gerektirir; yalnızca karşılaştırma amaçlı tutuluyor.
