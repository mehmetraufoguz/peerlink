#!/usr/bin/env python3
"""
socat deneyi: uzak makinedeki bir Claude oturumunun Unix socket'ini TCP üzerinden
yerel /tmp/cc-socks/ altına tünelle ve sahte bir registry kaydı ile "peer" gibi göster.

Uzak makinede (B) önce socket'i TCP'ye aç:
  socat TCP-LISTEN:7439,reuseaddr,fork UNIX-CONNECT:/tmp/cc-socks/<pid>.sock
  (token:  cat ~/.claude/sessions/<pid>.*.key)

Bu makinede (A):
  python3 socat_peer.py --name "B/Super Frontend" --remote-host 192.168.1.20 --remote-port 7439 --remote-token <token>

Not: Gönderen, token'ı YEREL key dosyasından okuyup uzak oturuma yollar; o yüzden
uzak oturumun gerçek token'ı buraya kopyalanmak zorunda. Çerçevedeki from="uds:/tmp/cc-socks/<A pid>.sock"
yeniden yazılmadığı için uzak taraf cevap veremez (A'daki yol B'de yok). Bu yüzden
üretimde socat değil, agent-relay relay tercih edilmeli.
"""
import argparse, hashlib, json, os, signal, subprocess, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True); ap.add_argument("--remote-host", required=True)
ap.add_argument("--remote-port", type=int, required=True); ap.add_argument("--remote-token", required=True)
a = ap.parse_args()

pid = os.getpid()
sess = os.path.expanduser("~/.claude/sessions")
sock = f"/tmp/cc-socks/{pid}.sock"
ps = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                    env={**os.environ, "TZ": "UTC"}).stdout.strip()
key = f"{sess}/{pid}.{hashlib.sha256(sock.encode()).hexdigest()}.key"
reg = f"{sess}/{pid}.json"
now = int(time.time() * 1000)

tunnel = subprocess.Popen(["socat", f"UNIX-LISTEN:{sock},fork,mode=600,unlink-early",
                           f"TCP:{a.remote_host}:{a.remote_port}"])
time.sleep(0.5)
with open(key, "w") as f: json.dump({"peerToken": a.remote_token, "procStart": ps, "pidDomain": "darwin"}, f)
os.chmod(key, 0o600)
with open(reg, "w") as f:
    json.dump({"pid": pid, "sessionId": "00000000-0000-4000-8000-%012d" % pid, "cwd": os.getcwd(), "startedAt": now,
               "procStart": ps, "version": "2.1.296", "peerProtocol": 1, "peerFeatures": [], "kind": "interactive",
               "entrypoint": "cli", "pidDomain": "darwin", "messagingSocketPath": sock, "name": a.name,
               "nameSource": "user", "nameSince": now, "status": "idle", "updatedAt": now, "statusUpdatedAt": now}, f)
print(f"socat peer '{a.name}' up: pid={pid} {sock} -> {a.remote_host}:{a.remote_port}", flush=True)

def bye(*_):
    tunnel.terminate()
    for p in (sock, key, reg):
        try: os.unlink(p)
        except FileNotFoundError: pass
    sys.exit(0)
for s in (signal.SIGINT, signal.SIGTERM): signal.signal(s, bye)
tunnel.wait(); bye()
