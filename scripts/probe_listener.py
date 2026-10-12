#!/usr/bin/env python3
"""Fake Claude Code peer session: registers itself like a real session and logs
every byte another session writes to its socket. Used to capture the wire format.
Usage: python3 probe_listener.py [--ack MODE]   MODE = none | json
"""
import os, sys, json, socket, hashlib, secrets, subprocess, time, signal, argparse

ap = argparse.ArgumentParser(); ap.add_argument("--ack", default="none"); ap.add_argument("--name", default="LAN-PROBE")
args = ap.parse_args()

pid = os.getpid()
home = os.path.expanduser("~/.claude/sessions")
sock_dir = "/tmp/cc-socks"
sock_path = f"{sock_dir}/{pid}.sock"
proc_start = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                            env={**os.environ, "TZ": "UTC"}).stdout.strip()
token = secrets.token_hex(16)
key_path = f"{home}/{pid}.{hashlib.sha256(sock_path.encode()).hexdigest()}.key"
reg_path = f"{home}/{pid}.json"
now = int(time.time() * 1000)
registry = {
    "pid": pid, "sessionId": "00000000-0000-4000-8000-%012d" % pid, "cwd": os.getcwd(),
    "startedAt": now, "procStart": proc_start, "version": "2.1.296", "peerProtocol": 1,
    "peerFeatures": ["notify_idle", "reply_across_default_dirs"], "kind": "interactive",
    "entrypoint": "cli", "pidDomain": "darwin", "messagingSocketPath": sock_path,
    "name": args.name, "nameSource": "user", "nameSince": now, "status": "idle",
    "updatedAt": now, "statusUpdatedAt": now,
}
log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.log"), "a", buffering=1)

def cleanup(*_):
    for p in (sock_path, key_path, reg_path):
        try: os.unlink(p)
        except FileNotFoundError: pass
    sys.exit(0)
for s in (signal.SIGINT, signal.SIGTERM): signal.signal(s, cleanup)

srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try: os.unlink(sock_path)
except FileNotFoundError: pass
srv.bind(sock_path); os.chmod(sock_path, 0o600); srv.listen(8)
with open(key_path, "w") as f: json.dump({"peerToken": token, "procStart": proc_start, "pidDomain": "darwin"}, f)
os.chmod(key_path, 0o600)
with open(reg_path, "w") as f: json.dump(registry, f)
print(f"listening pid={pid} sock={sock_path} name={args.name} ack={args.ack}", flush=True)
log.write(f"\n=== start pid={pid} {time.ctime()} token={token[:6]}... ack={args.ack}\n")

while True:
    conn, _ = srv.accept(); conn.settimeout(5)
    log.write(f"--- connection {time.ctime()}\n")
    buf = b""
    try:
        while True:
            chunk = conn.recv(65536)
            if not chunk: break
            buf += chunk
            log.write("RAW: " + repr(chunk) + "\n")
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip(): continue
                try: obj = json.loads(line)
                except Exception: obj = None
                if args.ack == "json" and obj:
                    if obj.get("type") == "auth":
                        reply = {"type": "auth_ok"}
                    else:
                        reply = {"type": "ack", "accepted": True, "msg_id": obj.get("msg_id") or obj.get("id")}
                    conn.sendall((json.dumps(reply) + "\n").encode()); log.write("SENT: " + json.dumps(reply) + "\n")
    except socket.timeout:
        log.write("(timeout, closing)\n")
    finally:
        conn.close()
