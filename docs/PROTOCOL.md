# Claude Code oturumlar arası mesajlaşma protokolü (gözlemle çıkarılmış)

Claude Code 2.1.296, macOS. Resmi belge yalnızca `CLAUDE_CODE_MESSAGING_SOCKET`,
`CLAUDE_CODE_MESSAGING_TOKEN` ve ilk satırın `{"type":"auth","token":...}` olduğunu söyler.
Gerisi `scripts/probe_listener.py` ile yakalandı.

## Kayıt defteri

Her oturum `~/.claude/sessions/<pid>.json` yazar:

```json
{"pid":1253,"sessionId":"<uuid>","cwd":"/path","startedAt":1791528156018,
 "procStart":"Fri Oct  9 06:42:32 2026","version":"2.1.295","peerProtocol":1,
 "peerFeatures":["notify_idle","reply_across_default_dirs","artifact_yield"],
 "kind":"interactive","entrypoint":"cli","pidDomain":"darwin",
 "messagingSocketPath":"/tmp/cc-socks/1253.sock","name":"...","nameSource":"user",
 "nameSince":1791528160817,"updatedAt":1791800012039,"status":"busy","statusUpdatedAt":1791800012039}
```

- `procStart` = `TZ=UTC ps -o lstart= -p <pid>` çıktısı. Eşleşmezse kayıt stale sayılır.
- Bilinmeyen ek alanlar (`peerlinkProxy`) yok sayılır.
- Yanında `<pid>.<sha256(messagingSocketPath)>.key` dosyası: `{"peerToken":"<32 hex>","procStart":"...","pidDomain":"darwin"}`, mod 600.
- Socket dizini beyaz listede olmalı: `/tmp/cc-socks(-N)?`, `/private/tmp/cc-socks(-N)?`, `/run/user/<uid>/cc-socks`.

`ListAgents`, kayıt defterini okur, pid canlılığını ve `procStart`'ı doğrular, bağlantı ile socket'i yoklar
(byte göndermeden). `status` alanı idle/busy olarak gösterilir.

## Tel formatı

Gönderen, alıcının `.key` dosyasından token'ı okur, socket'e bağlanır, iki satır newline-delimited JSON
yazar ve bağlantıyı kapatır. Ack beklemez.

```
{"type":"auth","token":"<alıcının peerToken'ı>"}
{"msgV":1,"msg_id":"<uuid>","type":"user",
 "message":{"role":"user","content":"<cross-session-message from=\"uds:/tmp/cc-socks/<gönderen pid>.sock\" from-session=\"<host session id>\" from-name=\"<gönderen adı>\" from-mode=\"prompting\">\n<metin>\n</cross-session-message>"},
 "priority":"next","from":"uds:/tmp/cc-socks/<gönderen pid>.sock"}
```

- Alıcı, token eşleşmezse sessizce düşürür (cevap yok).
- Alıcı Claude, cevap vermek için `from` özniteliğini `to` olarak kullanır; bu yüzden relay, teslimatta
  `from` ve `from-name` değerlerini karşı taraftaki proxy'ye göre yeniden yazar.
- Binary'de görülen diğer çerçeve türü: `type:"control"` (içeriği incelenmedi).
- `msg_id` tekrarları alıcı tarafında "duplicate" olarak düşürülebilir; relay orijinal `msg_id`'yi korur.
