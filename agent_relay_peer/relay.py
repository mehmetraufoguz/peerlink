#!/usr/bin/env python3
"""
agent-relay-peer — Claude Code oturumlarını LAN üzerinden birbirine bağlayan relay.

Her makinede bir relay çalışır. Relay, eş makinelerdeki oturumları periyodik olarak sorar
ve her uzak oturum için YEREL bir "proxy oturum" (sahte peer) kaydeder:
    ~/.claude/sessions/<proxy-pid>.json  +  /tmp/cc-socks/<proxy-pid>.sock
Böylece uzak oturumlar ListAgents'ta "<etiket>/<isim>" olarak görünür ve SendMessage ile
normal şekilde yazılabilir. Proxy socket'ine gelen çerçeve HTTP ile karşı relay'e gider;
karşı relay onu gerçek oturumun Unix socket'ine native protokolle teslim eder.
Cevaplar aynı yolu ters yönde izler (from alanı her iki tarafta proxy yoluna yeniden yazılır).

Komutlar:
  agent-relay relay     [--secret S] [--peer HOST[:PORT]]... [--port 7437] [--label NAME]
  agent-relay status    yerel relay çalışıyor mu, eşler erişilebilir mi, hangi proxy'ler var
  agent-relay sessions  --host HOST       uzak makinedeki oturumları listele
  agent-relay send      --host HOST --to "oturum adı" "metin"   proxy'siz tek seferlik gönderim
  agent-relay clean     ölü proxy kayıtlarını temizle
  agent-relay proxy     (dahili; relay tarafından spawn edilir)

Yapılandırma: ~/.config/agent-relay-peer/config.json
  {"secret": "...", "peers": ["192.168.1.20"], "label": "macA", "port": 7437,
   "bind": "0.0.0.0", "advertise": null, "interval": 5}
CLI argümanları dosyayı, AGENT_RELAY_SECRET ortam değişkeni secret alanını geçersiz kılar.

Güvenlik: paylaşılan gizli anahtar (X-Agent-Relay-Secret başlığı). HTTP düz metin — yalnızca
güvendiğin LAN'da kullan; istersen önüne bir TLS reverse proxy koy.
"""
import argparse, glob, hashlib, http.client, json, os, re, secrets, signal, socket, subprocess, sys, threading, time, urllib.parse, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.1.0"

SESS_DIR = os.path.expanduser("~/.claude/sessions")
SOCK_DIR = "/tmp/cc-socks"
PROXY_MARK = "agentRelayProxy"
SECRET_HEADER = "X-Agent-Relay-Secret"
CONFIG_PATH = os.path.expanduser("~/.config/agent-relay-peer/config.json")
DEFAULT_PORT = 7437
HOSTNAME = socket.gethostname().split(".")[0]
CLAUDE_VERSION_TESTED = "2.1.296"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- config
def load_config(path=None):
    p = path or CONFIG_PATH
    try:
        with open(p) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        sys.exit(f"config okunamadı ({p}): {e}")


def resolve(a, cfg, key, default=None, env=None):
    v = getattr(a, key, None)
    if v not in (None, [], False):
        return v
    if env and os.environ.get(env):
        return os.environ[env]
    return cfg.get(key, default)


# ---------------------------------------------------------------- registry helpers
def proc_start(pid: int) -> str:
    return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                          env={**os.environ, "TZ": "UTC"}).stdout.strip()


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False


def pid_domain() -> str:
    return "darwin" if sys.platform == "darwin" else "linux"


def local_sessions(include_proxies=False):
    out = []
    for p in glob.glob(f"{SESS_DIR}/*.json"):
        try:
            r = json.load(open(p))
        except Exception:
            continue
        if not pid_alive(int(r.get("pid", 0))):
            continue
        if not include_proxies and r.get(PROXY_MARK):
            continue
        out.append({"pid": r["pid"], "name": r.get("name") or f"session-{r['pid']}", "status": r.get("status"),
                    "cwd": r.get("cwd"), "kind": r.get("kind"), "sock": r.get("messagingSocketPath"),
                    "sessionId": r.get("sessionId"), "proxy": r.get(PROXY_MARK)})
    return out


def key_path_for(pid: int, sock_path: str) -> str:
    return f"{SESS_DIR}/{pid}.{hashlib.sha256(sock_path.encode()).hexdigest()}.key"


def read_token(pid: int, sock_path: str) -> str:
    return json.load(open(key_path_for(pid, sock_path)))["peerToken"]


def deliver_local(target: dict, frame: dict):
    """Native teslimat: auth satırı + çerçeve, alıcının Unix socket'ine."""
    token = read_token(target["pid"], target["sock"])
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(5)
    s.connect(target["sock"])
    s.sendall((json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps(frame) + "\n").encode())
    s.close()


def make_frame(content: str, from_path: str, msg_id=None):
    return {"msgV": 1, "msg_id": msg_id or str(uuid.uuid4()), "type": "user",
            "message": {"role": "user", "content": content}, "priority": "next", "from": f"uds:{from_path}"}


def wrap_content(text: str, from_path: str, from_name: str, from_session="agent-relay") -> str:
    return (f'<cross-session-message from="uds:{from_path}" from-session="{from_session}" '
            f'from-name="{from_name}" from-mode="prompting">\n{text}\n</cross-session-message>')


def rewrite_from(content: str, new_from_path: str, new_from_name: str) -> str:
    content = re.sub(r'from="uds:[^"]*"', f'from="uds:{new_from_path}"', content, count=1)
    content = re.sub(r'from-name="[^"]*"', f'from-name="{new_from_name}"', content, count=1)
    return content


def find_session(sessions, needle: str):
    needle_l = needle.lower()
    exact = [s for s in sessions if s["name"].lower() == needle_l or str(s["pid"]) == needle]
    if exact: return exact[0]
    part = [s for s in sessions if needle_l in s["name"].lower()]
    return part[0] if len(part) == 1 else None


# ---------------------------------------------------------------- HTTP client
def http_json(host, port, secret, method, path, body=None, timeout=5):
    c = http.client.HTTPConnection(host, port, timeout=timeout)
    data = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers={SECRET_HEADER: secret, "Content-Type": "application/json"})
    r = c.getresponse(); raw = r.read()
    if r.status >= 300: raise RuntimeError(f"{host}:{port}{path} -> {r.status} {raw[:200]!r}")
    return json.loads(raw or b"{}")


def split_host(h, default_port):
    host, _, port = h.partition(":")
    return host, int(port or default_port)


# ---------------------------------------------------------------- proxy (fake peer) process
class FakePeer:
    """Yerel registry'ye sahte bir oturum olarak kaydolur ve socket'ine gelen çerçeveleri verir."""

    def __init__(self, name: str, extra: dict):
        self.pid = os.getpid()
        self.sock_path = f"{SOCK_DIR}/{self.pid}.sock"
        self.reg_path = f"{SESS_DIR}/{self.pid}.json"
        self.key_path = key_path_for(self.pid, self.sock_path)
        ps = proc_start(self.pid); now = int(time.time() * 1000)
        self.token = secrets.token_hex(16)
        self.registry = {"pid": self.pid, "sessionId": str(uuid.uuid4()), "cwd": os.getcwd(), "startedAt": now,
                         "procStart": ps, "version": CLAUDE_VERSION_TESTED, "peerProtocol": 1,
                         "peerFeatures": ["reply_across_default_dirs"], "kind": "interactive", "entrypoint": "cli",
                         "pidDomain": pid_domain(), "messagingSocketPath": self.sock_path, "name": name,
                         "nameSource": "user", "nameSince": now, "status": "idle", "updatedAt": now,
                         "statusUpdatedAt": now, PROXY_MARK: extra}
        os.makedirs(SOCK_DIR, mode=0o700, exist_ok=True)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try: os.unlink(self.sock_path)
        except FileNotFoundError: pass
        self.srv.bind(self.sock_path); os.chmod(self.sock_path, 0o600); self.srv.listen(16)
        with open(self.key_path, "w") as f: json.dump({"peerToken": self.token, "procStart": ps, "pidDomain": pid_domain()}, f)
        os.chmod(self.key_path, 0o600)
        with open(self.reg_path, "w") as f: json.dump(self.registry, f)

    def set_status(self, status):
        if self.registry.get("status") != status:
            self.registry["status"] = status
            self.registry["statusUpdatedAt"] = self.registry["updatedAt"] = int(time.time() * 1000)
            with open(self.reg_path, "w") as f: json.dump(self.registry, f)

    def cleanup(self):
        for p in (self.sock_path, self.key_path, self.reg_path):
            try: os.unlink(p)
            except FileNotFoundError: pass

    def frames(self):
        """Yield every authenticated frame received."""
        while True:
            conn, _ = self.srv.accept(); conn.settimeout(5)
            buf, authed = b"", False
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk: break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip(): continue
                        try: obj = json.loads(line)
                        except Exception: continue
                        if obj.get("type") == "auth":
                            authed = obj.get("token") == self.token
                            if not authed: log("proxy: bad token, dropping connection"); break
                            continue
                        if authed: yield obj
            except socket.timeout:
                pass
            finally:
                conn.close()


def cmd_proxy(a):
    """Uzak oturum (a.remote_host, a.remote_pid) için yerel sahte peer. Gelen çerçeveleri karşı relay'e iletir."""
    peer = FakePeer(a.name, {"host": a.remote_host, "port": a.remote_port, "remotePid": a.remote_pid,
                             "remoteName": a.remote_name, "label": a.remote_label})
    def bye(*_): peer.cleanup(); sys.exit(0)
    for s in (signal.SIGINT, signal.SIGTERM): signal.signal(s, bye)
    log(f"proxy up: '{a.name}' pid={peer.pid} -> {a.remote_host}:{a.remote_port} pid {a.remote_pid}")

    def mirror():
        misses = 0
        while True:
            try:
                st = http_json(a.remote_host, a.remote_port, a.secret, "GET", "/sessions")
                me = next((s for s in st["sessions"] if s["pid"] == a.remote_pid), None)
                if me is None: log("remote session gone, exiting"); bye()
                peer.set_status(me.get("status") or "idle"); misses = 0
            except SystemExit:
                raise
            except Exception as e:
                misses += 1
                if misses in (1, 12): log("mirror:", e)
                if misses >= 60: log("remote relay unreachable for too long, exiting"); bye()
            time.sleep(a.interval)
    threading.Thread(target=mirror, daemon=True).start()

    for frame in peer.frames():
        try:
            content = frame["message"]["content"]
            m = re.search(r'from="uds:([^"]+)"', content)
            sender_sock = m.group(1) if m else frame.get("from", "").removeprefix("uds:")
            sender = next((s for s in local_sessions() if s["sock"] == sender_sock), None)
            body = {"to_pid": a.remote_pid, "content": content, "msg_id": frame.get("msg_id"),
                    "origin": {"host": a.local_host, "port": a.local_port, "pid": sender["pid"] if sender else None,
                               "name": sender["name"] if sender else "unknown", "label": a.label}}
            r = http_json(a.remote_host, a.remote_port, a.secret, "POST", "/deliver", body)
            log(f"forwarded {str(frame.get('msg_id', '?'))[:8]} from '{body['origin']['name']}' -> remote pid {a.remote_pid}: {r}")
        except Exception as e:
            log("forward failed:", e)


# ---------------------------------------------------------------- relay
class Relay:
    def __init__(self, a):
        self.a = a
        self.proxies = {}  # (host, remote_pid) -> Popen
        self.lock = threading.Lock()

    @staticmethod
    def proxy_name(label, remote_name):
        return f"{label}/{remote_name}"

    def ensure_proxy(self, host, port, remote_pid, remote_name, label):
        # Anahtar (etiket, uzak pid): aynı eşe farklı adreslerle (IP, hostname, loopback) ulaşılsa da tek proxy.
        key = (label, remote_pid)
        with self.lock:
            p = self.proxies.get(key)
            if p and p.poll() is None: return
            cmd = [sys.executable, os.path.abspath(__file__), "proxy", "--name", self.proxy_name(label, remote_name),
                   "--remote-host", host, "--remote-port", str(port), "--remote-pid", str(remote_pid),
                   "--remote-name", remote_name, "--remote-label", label, "--secret", self.a.secret,
                   "--local-host", self.a.advertise, "--local-port", str(self.a.port), "--label", self.a.label,
                   "--interval", str(self.a.interval)]
            self.proxies[key] = subprocess.Popen(cmd)
            log(f"spawned proxy for {label}/{remote_name} (remote pid {remote_pid})")

    def proxy_sock_for(self, label, remote_pid):
        for s in local_sessions(include_proxies=True):
            pr = s.get("proxy")
            if pr and pr.get("label") == label and pr.get("remotePid") == remote_pid:
                return s
        return None

    def reconcile(self):
        while True:
            for host in self.a.peer:
                h, prt = split_host(host, self.a.port)
                try:
                    r = http_json(h, prt, self.a.secret, "GET", "/sessions")
                except Exception as e:
                    log(f"peer {h}:{prt} unreachable: {e}"); continue
                label = r.get("label", h)
                live = set()
                for s in r["sessions"]:
                    live.add((label, s["pid"]))
                    self.ensure_proxy(h, prt, s["pid"], s["name"], label)
                with self.lock:
                    for key, p in list(self.proxies.items()):
                        if key[0] == label and key not in live:
                            p.terminate(); self.proxies.pop(key, None); log(f"removed proxy {key}")
            time.sleep(self.a.interval)

    def deliver(self, body):
        target = next((s for s in local_sessions() if s["pid"] == body["to_pid"]), None)
        if not target: return {"ok": False, "error": "target session not found"}
        o = body["origin"]
        px = None
        if o.get("pid") is not None and o.get("host"):
            label = o.get("label", o["host"])
            self.ensure_proxy(o["host"], o["port"], o["pid"], o["name"], label)
            for _ in range(30):
                px = self.proxy_sock_for(label, o["pid"])
                if px: break
                time.sleep(0.1)
        from_path = px["sock"] if px else f"{SOCK_DIR}/agent-relay-noreply.sock"
        from_name = self.proxy_name(o.get("label", o.get("host") or "remote"), o["name"])
        content = rewrite_from(body["content"], from_path, from_name)
        deliver_local(target, make_frame(content, from_path, body.get("msg_id")))
        log(f"delivered to '{target['name']}' (pid {target['pid']}) from {from_name}")
        return {"ok": True, "delivered_to": target["name"]}

    def serve(self):
        relay = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _auth(self):
                if self.headers.get(SECRET_HEADER) != relay.a.secret:
                    self.send_response(401); self.end_headers(); return False
                return True
            def _json(self, code, obj):
                b = json.dumps(obj).encode(); self.send_response(code)
                self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b)
            def do_GET(self):
                path = urllib.parse.urlparse(self.path).path
                if path == "/health":
                    return self._json(200, {"ok": True, "label": relay.a.label, "version": __version__,
                                            "peers": relay.a.peer, "proxies": len(relay.proxies)})
                if not self._auth(): return
                if path == "/sessions":
                    return self._json(200, {"label": relay.a.label, "sessions": local_sessions()})
                self._json(404, {"error": "not found"})
            def do_POST(self):
                if not self._auth(): return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/deliver":
                    try: return self._json(200, relay.deliver(body))
                    except Exception as e: return self._json(500, {"ok": False, "error": str(e)})
                self._json(404, {"error": "not found"})

        srv = ThreadingHTTPServer((self.a.bind, self.a.port), H)
        log(f"relay '{self.a.label}' v{__version__} listening on {self.a.bind}:{self.a.port}, "
            f"advertise={self.a.advertise}, peers={self.a.peer}")
        threading.Thread(target=self.reconcile, daemon=True).start()
        def bye(*_):
            for p in self.proxies.values(): p.terminate()
            sys.exit(0)
        for s in (signal.SIGINT, signal.SIGTERM): signal.signal(s, bye)
        srv.serve_forever()


# ---------------------------------------------------------------- one-shot CLI
def cmd_status(a, cfg):
    port = resolve(a, cfg, "port", DEFAULT_PORT)
    secret = resolve(a, cfg, "secret", env="AGENT_RELAY_SECRET")
    print(f"config: {CONFIG_PATH if os.path.exists(CONFIG_PATH) else '(yok)'}")
    try:
        h = http_json("127.0.0.1", port, secret or "", "GET", "/health", timeout=2)
        print(f"local relay: UP  label={h['label']} v{h['version']} peers={h['peers']} proxies={h['proxies']}")
    except Exception as e:
        print(f"local relay: DOWN on 127.0.0.1:{port} ({e.__class__.__name__})")
    sess = local_sessions(include_proxies=True)
    real = [s for s in sess if not s["proxy"]]; prox = [s for s in sess if s["proxy"]]
    print(f"\nlocal sessions ({len(real)}):")
    for s in real: print(f"  {s['pid']:>6}  {s['status'] or '-':<6} {s['name']}")
    print(f"\nremote proxies ({len(prox)}):")
    for s in prox: print(f"  {s['pid']:>6}  {s['status'] or '-':<6} {s['name']}   <- {s['proxy']['host']}:{s['proxy']['port']} pid {s['proxy']['remotePid']}")
    peers = resolve(a, cfg, "peer", []) or cfg.get("peers", [])
    if peers and secret:
        print("\npeers:")
        for p in peers:
            h, prt = split_host(p, port)
            try:
                r = http_json(h, prt, secret, "GET", "/sessions", timeout=3)
                print(f"  {h}:{prt}  UP  label={r['label']}  sessions={len(r['sessions'])}")
            except Exception as e:
                print(f"  {h}:{prt}  DOWN ({e})")


def cmd_sessions(a, cfg):
    secret = resolve(a, cfg, "secret", env="AGENT_RELAY_SECRET") or sys.exit("secret gerekli (--secret / config / AGENT_RELAY_SECRET)")
    h, prt = split_host(a.host, resolve(a, cfg, "port", DEFAULT_PORT))
    r = http_json(h, prt, secret, "GET", "/sessions")
    print(f"[{r.get('label')}]")
    for s in r["sessions"]: print(f"  {s['pid']:>6}  {s['status'] or '-':<6} {s['name']}   ({s['cwd']})")


def cmd_send(a, cfg):
    secret = resolve(a, cfg, "secret", env="AGENT_RELAY_SECRET") or sys.exit("secret gerekli (--secret / config / AGENT_RELAY_SECRET)")
    h, prt = split_host(a.host, resolve(a, cfg, "port", DEFAULT_PORT))
    r = http_json(h, prt, secret, "GET", "/sessions")
    t = find_session(r["sessions"], a.to)
    if not t: sys.exit(f"'{a.to}' için tek eşleşme yok. Oturumlar: {[s['name'] for s in r['sessions']]}")
    label = resolve(a, cfg, "label", HOSTNAME)
    content = wrap_content(a.text, f"{SOCK_DIR}/agent-relay-noreply.sock", f"{label}/cli")
    body = {"to_pid": t["pid"], "content": content,
            "origin": {"host": "", "port": 0, "pid": None, "name": "cli", "label": label}}
    print(http_json(h, prt, secret, "POST", "/deliver", body))


def cmd_clean(a, cfg):
    n = 0
    for p in glob.glob(f"{SESS_DIR}/*.json"):
        try: r = json.load(open(p))
        except Exception: continue
        if r.get(PROXY_MARK) and not pid_alive(int(r.get("pid", 0))):
            for f in (p, key_path_for(r["pid"], r["messagingSocketPath"]), r["messagingSocketPath"]):
                try: os.unlink(f); n += 1
                except FileNotFoundError: pass
    print(f"removed {n} stale proxy file(s)")


def cmd_relay(a, cfg):
    a.secret = resolve(a, cfg, "secret", env="AGENT_RELAY_SECRET") or sys.exit("secret gerekli (--secret / config / AGENT_RELAY_SECRET)")
    a.peer = resolve(a, cfg, "peer", []) or cfg.get("peers", [])
    a.port = int(resolve(a, cfg, "port", DEFAULT_PORT))
    a.bind = resolve(a, cfg, "bind", "0.0.0.0")
    a.label = resolve(a, cfg, "label", HOSTNAME)
    a.interval = float(resolve(a, cfg, "interval", 5))
    a.advertise = resolve(a, cfg, "advertise")
    if not a.advertise:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); a.advertise = s.getsockname()[0]
        except Exception:
            a.advertise = "127.0.0.1"
    if not a.peer: log("uyarı: hiç eş (--peer) verilmedi; relay gelen mesajları kabul eder ama proxy oluşturmaz")
    Relay(a).serve()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agent-relay", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help=f"yapılandırma dosyası (varsayılan {CONFIG_PATH})")
    ap.add_argument("--version", action="version", version=f"agent-relay-peer {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("relay", help="relay'i başlat"); r.add_argument("--secret"); r.add_argument("--peer", action="append", default=[])
    r.add_argument("--port", type=int); r.add_argument("--bind"); r.add_argument("--advertise", help="bu makinenin LAN IP'si")
    r.add_argument("--label"); r.add_argument("--interval", type=float)

    p = sub.add_parser("proxy")
    for f in ("--name", "--remote-host", "--remote-name", "--remote-label", "--secret", "--local-host", "--label"): p.add_argument(f, required=True)
    p.add_argument("--remote-port", type=int, required=True); p.add_argument("--remote-pid", type=int, required=True)
    p.add_argument("--local-port", type=int, required=True); p.add_argument("--interval", type=float, default=5)

    st = sub.add_parser("status", help="durum"); st.add_argument("--secret"); st.add_argument("--port", type=int); st.add_argument("--peer", action="append", default=[])
    s = sub.add_parser("sessions", help="uzak oturumları listele"); s.add_argument("--secret"); s.add_argument("--host", required=True); s.add_argument("--port", type=int)
    d = sub.add_parser("send", help="tek seferlik mesaj"); d.add_argument("--secret"); d.add_argument("--host", required=True); d.add_argument("--port", type=int)
    d.add_argument("--label"); d.add_argument("--to", required=True); d.add_argument("text")
    sub.add_parser("clean", help="ölü proxy kayıtlarını sil")

    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    {"relay": cmd_relay, "proxy": lambda a, c: cmd_proxy(a), "status": cmd_status, "sessions": cmd_sessions,
     "send": cmd_send, "clean": cmd_clean}[a.cmd](a, cfg)


if __name__ == "__main__":
    main()
