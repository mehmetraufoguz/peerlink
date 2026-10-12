#!/usr/bin/env python3
"""
peerlink — Claude Code oturumlarını LAN üzerinden birbirine bağlayan relay.

Her makinede bir relay çalışır. Relay, eş makinelerdeki oturumları periyodik olarak sorar
ve her uzak oturum için YEREL bir "proxy oturum" (sahte peer) kaydeder:
    ~/.claude/sessions/<proxy-pid>.json  +  /tmp/cc-socks/<proxy-pid>.sock
Böylece uzak oturumlar ListAgents'ta "<etiket>/<isim>" olarak görünür ve SendMessage ile
normal şekilde yazılabilir. Proxy socket'ine gelen çerçeve HTTP ile karşı relay'e gider;
karşı relay onu gerçek oturumun Unix socket'ine native protokolle teslim eder.
Cevaplar aynı yolu ters yönde izler (from alanı her iki tarafta proxy yoluna yeniden yazılır).

Komutlar:
  peerlink up [-d] relay'i başlat (-d: arka planda, log ~/.peerlink.log)
  peerlink down    relay'i durdur;  peerlink restart  config değişince
  peerlink who     kim var: relay durumu, yerel oturumlar, proxy'ler, eşlerdeki oturumlar  [--host HOST]
  peerlink send    macB/"Oturum adı" "metin"   proxy'siz tek seferlik gönderim (cevap dönmez)
  peerlink doctor  ortamı denetle (python, config, relay, eşler, güvenlik duvarı, ölü proxy'ler)
  peerlink clean   ölü proxy kayıtlarını temizle
  peerlink proxy   (dahili; relay tarafından spawn edilir)

Yapılandırma: ~/.config/peerlink/config.json
  {"secret": "...", "peers": ["192.168.1.20"], "label": "macA", "port": 7437,
   "bind": "0.0.0.0", "advertise": null, "interval": 5}
CLI argümanları dosyayı, PEERLINK_SECRET ortam değişkeni secret alanını geçersiz kılar.

Güvenlik: paylaşılan gizli anahtar (X-Peerlink-Secret başlığı). HTTP düz metin — yalnızca
güvendiğin LAN'da kullan; istersen önüne bir TLS reverse proxy koy.
"""
import argparse, glob, hashlib, http.client, json, os, re, secrets, signal, socket, subprocess, sys, threading, time, urllib.parse, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.3.0"

SESS_DIR = os.path.expanduser("~/.claude/sessions")
SOCK_DIR = "/tmp/cc-socks"
PROXY_MARK = "peerlinkProxy"
SECRET_HEADER = "X-Peerlink-Secret"
CONFIG_PATH = os.path.expanduser("~/.config/peerlink/config.json")
PID_PATH = os.path.expanduser("~/.config/peerlink/relay.pid")
LOG_PATH = os.path.expanduser("~/.peerlink.log")
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


def wrap_content(text: str, from_path: str, from_name: str, from_session="peerlink") -> str:
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
    a.secret = a.secret or os.environ.get("PEERLINK_SECRET") or sys.exit("proxy: secret yok (PEERLINK_SECRET)")
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
                   "--remote-name", remote_name, "--remote-label", label,
                   "--local-host", self.a.advertise, "--local-port", str(self.a.port), "--label", self.a.label,
                   "--interval", str(self.a.interval)]
            # secret komut satırına değil ortam değişkenine (ps'te görünmesin)
            self.proxies[key] = subprocess.Popen(cmd, env={**os.environ, "PEERLINK_SECRET": self.a.secret})
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
        from_path = px["sock"] if px else f"{SOCK_DIR}/peerlink-noreply.sock"
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
def need_secret(a, cfg):
    return resolve(a, cfg, "secret", env="PEERLINK_SECRET") or sys.exit("secret gerekli (--secret / config / PEERLINK_SECRET)")


def peer_list(a, cfg):
    return resolve(a, cfg, "peer", []) or cfg.get("peers", [])


def print_sessions(sessions, indent="  "):
    for s in sessions:
        print(f"{indent}{s['pid']:>6}  {s['status'] or '-':<6} {s['name']}")


def cmd_who(a, cfg):
    """Kim var? Yerel relay, yerel oturumlar, proxy'ler ve eşlerdeki oturumlar."""
    port = resolve(a, cfg, "port", DEFAULT_PORT)
    secret = resolve(a, cfg, "secret", env="PEERLINK_SECRET")
    if a.host:  # yalnızca uzak makine
        h, prt = split_host(a.host, port)
        r = http_json(h, prt, secret or sys.exit("secret gerekli"), "GET", "/sessions")
        print(f"{r.get('label')} ({h}:{prt}):"); print_sessions(r["sessions"]); return
    try:
        h = http_json("127.0.0.1", port, "", "GET", "/health", timeout=2)
        print(f"relay: UP  label={h['label']} v{h['version']} peers={h['peers']} proxies={h['proxies']}")
    except Exception as e:
        print(f"relay: DOWN on 127.0.0.1:{port} ({e.__class__.__name__})  -> 'peerlink up' ile başlat")
    sess = local_sessions(include_proxies=True)
    real = [s for s in sess if not s["proxy"]]; prox = [s for s in sess if s["proxy"]]
    print(f"\nlocal ({len(real)}):"); print_sessions(real)
    print(f"\nremote via proxy ({len(prox)}):")
    for s in prox:
        print(f"  {s['pid']:>6}  {s['status'] or '-':<6} {s['name']}   <- {s['proxy']['host']}:{s['proxy']['port']} pid {s['proxy']['remotePid']}")
    peers = peer_list(a, cfg)
    if peers:
        print("\npeers:")
        for pth in peers:
            h, prt = split_host(pth, port)
            try:
                r = http_json(h, prt, secret or "", "GET", "/sessions", timeout=3)
                print(f"  {h}:{prt}  UP  label={r['label']}"); print_sessions(r["sessions"], indent="      ")
            except Exception as e:
                print(f"  {h}:{prt}  DOWN ({e})")


def resolve_target(a, cfg):
    """'label/isim' veya --host + isim -> (host, port, relay_sessions, hedef)."""
    port = resolve(a, cfg, "port", DEFAULT_PORT); secret = need_secret(a, cfg)
    name = a.to
    if a.host:
        h, prt = split_host(a.host, port)
        r = http_json(h, prt, secret, "GET", "/sessions")
    elif "/" in a.to:
        label, name = a.to.split("/", 1)
        for pth in peer_list(a, cfg):
            h, prt = split_host(pth, port)
            try: r = http_json(h, prt, secret, "GET", "/sessions", timeout=3)
            except Exception: continue
            if r.get("label") == label: break
        else:
            sys.exit(f"'{label}' etiketli eş bulunamadı. Eşler: {peer_list(a, cfg) or '(config yok)'}")
    else:
        sys.exit("hedefi 'etiket/oturum adı' olarak ver ya da --host kullan")
    t = find_session(r["sessions"], name)
    if not t: sys.exit(f"'{name}' için tek eşleşme yok. Oturumlar: {[s['name'] for s in r['sessions']]}")
    return h, prt, secret, t


def cmd_send(a, cfg):
    """Proxy'siz tek seferlik gönderim (cevap yolu yoktur)."""
    h, prt, secret, t = resolve_target(a, cfg)
    label = resolve(a, cfg, "label", HOSTNAME)
    content = wrap_content(a.text, f"{SOCK_DIR}/peerlink-noreply.sock", f"{label}/cli")
    body = {"to_pid": t["pid"], "content": content,
            "origin": {"host": "", "port": 0, "pid": None, "name": "cli", "label": label}}
    r = http_json(h, prt, secret, "POST", "/deliver", body)
    print(f"-> {t['name']} @ {h}:{prt}: {'ok' if r.get('ok') else r}")


def cmd_doctor(a, cfg):
    """Ortamı denetle; her satır ok/warn/FAIL ile başlar, sonda sonraki adım önerilir. Çıkış kodu: FAIL varsa 1."""
    fails, warns, hints = [], [], []
    def ok(m, _hint=None): print(f"  ok    {m}")
    def warn(m, hint=None): warns.append(m); print(f"  warn  {m}"); hint and hints.append(hint)
    def fail(m, hint=None): fails.append(m); print(f"  FAIL  {m}"); hint and hints.append(hint)

    print("peerlink doctor")
    v = sys.version_info
    (ok if v >= (3, 9) else fail)(f"python {v.major}.{v.minor}" + ("" if v >= (3, 9) else " (3.9+ gerekli)"))
    try:
        cv = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=5).stdout.strip().split()[0]
        (ok if cv == CLAUDE_VERSION_TESTED else warn)(f"claude {cv}" + ("" if cv == CLAUDE_VERSION_TESTED else f" (test edilen {CLAUDE_VERSION_TESTED}; proxy'ler ListAgents'ta görünmezse protokol değişmiş olabilir)"))
    except Exception:
        warn("claude CLI bulunamadı (PATH'te değil?)")
    bindir = os.path.expanduser("~/.local/bin")
    if not any(os.path.exists(os.path.join(d, "peerlink")) for d in os.environ.get("PATH", "").split(":")):
        warn("'peerlink' PATH'te yok", f"export PATH=\"{bindir}:$PATH\"  (veya python3 -m peerlink / python3 ~/.peerlink/peerlink/relay.py kullan)")
    else:
        ok("peerlink PATH'te")

    port = resolve(a, cfg, "port", DEFAULT_PORT)
    secret = resolve(a, cfg, "secret", env="PEERLINK_SECRET")
    peers = peer_list(a, cfg)
    label = resolve(a, cfg, "label", HOSTNAME)
    if not os.path.exists(CONFIG_PATH):
        fail(f"config yok: {CONFIG_PATH}", "install.sh --secret <S> --peer <IP> --label <ad>   (kullanıcı kendisi çalıştırsın; anahtar sohbete yazılmasın)")
    else:
        ok(f"config: {CONFIG_PATH}")
        if not secret: fail("config'te secret yok")
        elif secret.startswith("DEGISTIR"): fail("config'teki secret örnek değer", "gerçek anahtar: openssl rand -hex 24  (iki makinede aynı)")
        if not peers: fail("config'te peers boş", "en az bir eş IP'si gerekli: --peer 192.168.1.20")
        if label == HOSTNAME and not cfg.get("label"): warn(f"label verilmedi, hostname kullanılacak: {HOSTNAME}")

    if os.path.isdir(SOCK_DIR):
        st = os.stat(SOCK_DIR)
        (ok if st.st_uid == os.getuid() and (st.st_mode & 0o077) == 0 else fail)(f"{SOCK_DIR} (uid={st.st_uid}, mode={oct(st.st_mode & 0o777)})")
    else:
        warn(f"{SOCK_DIR} yok: bu makinede hiç Claude Code oturumu çalışmıyor")
    sess = local_sessions(include_proxies=True)
    real = [s for s in sess if not s["proxy"]]; prox = [s for s in sess if s["proxy"]]
    (ok if real else warn)(f"yerel oturum: {len(real)}" + ("" if real else " (uzak taraf sana yazamaz, sen yazabilirsin)"))
    stale = 0
    for p in glob.glob(f"{SESS_DIR}/*.json"):
        try: r = json.load(open(p))
        except Exception: continue
        if r.get(PROXY_MARK) and not pid_alive(int(r.get("pid", 0))): stale += 1
    (warn if stale else ok)(f"ölü proxy kaydı: {stale}", "peerlink clean" if stale else None)

    relay_up = False
    try:
        h = http_json("127.0.0.1", port, "", "GET", "/health", timeout=2); relay_up = True
        ok(f"relay UP: label={h['label']} v{h['version']} proxies={h['proxies']}")
        if h["label"] != label: warn(f"çalışan relay'in etiketi ({h['label']}) config'ten ({label}) farklı; eski config ile başlatılmış")
    except Exception as e:
        fail(f"relay DOWN (127.0.0.1:{port}: {e.__class__.__name__})", "peerlink up -d")
    ok(f"proxy oturum: {len(prox)}") if prox else (warn("hiç proxy yok: eşler erişilemiyor ya da orada oturum yok") if relay_up else None)

    for pth in peers:
        h, prt = split_host(pth, port)
        try:
            r = http_json(h, prt, secret or "", "GET", "/sessions", timeout=3)
            if r.get("label") == label: fail(f"eş {h}:{prt} etiketi seninle aynı ({label}); etiketler makine başına benzersiz olmalı", "--label farklı bir ad")
            else: ok(f"eş {h}:{prt} UP label={r['label']} oturum={len(r['sessions'])}")
        except RuntimeError as e:
            if "401" in str(e): fail(f"eş {h}:{prt} 401: gizli anahtar uyuşmuyor", "iki makinede aynı secret olmalı")
            else: fail(f"eş {h}:{prt} hata: {e}")
        except socket.timeout:
            fail(f"eş {h}:{prt} zaman aşımı: IP yanlış, makine kapalı ya da güvenlik duvarı {prt} portunu kesiyor", "karşı makinede: peerlink doctor; macOS güvenlik duvarında python3'e gelen bağlantı izni")
        except ConnectionRefusedError:
            fail(f"eş {h}:{prt} bağlantı reddedildi: makine açık ama relay çalışmıyor", "karşı makinede: peerlink up")
        except OSError as e:
            if e.errno in (65, 51, 113, 101):  # EHOSTUNREACH / ENETUNREACH (macOS, Linux)
                fail(f"eş {h}:{prt} ağda yok (no route to host): IP yanlış ya da makine bu ağda değil",
                     "karşı makinede IP'yi doğrula: ipconfig getifaddr en0  (macOS) / hostname -I (Linux); sonra config'teki peers'ı düzelt")
            else:
                fail(f"eş {h}:{prt} {e.__class__.__name__}: {e}")
        except Exception as e:
            fail(f"eş {h}:{prt} {e.__class__.__name__}: {e}")

    print()
    if hints:
        print("sonraki adım:"); [print(f"  - {x}") for x in dict.fromkeys(hints)]
    print(f"{'SORUN VAR' if fails else 'HAZIR'}: {len(fails)} hata, {len(warns)} uyarı")
    sys.exit(1 if fails else 0)


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


def relay_health(port, timeout=1.5):
    try: return http_json("127.0.0.1", port, "", "GET", "/health", timeout=timeout)
    except Exception: return None


def read_pid():
    try: return int(open(PID_PATH).read().strip())
    except Exception: return None


def cmd_up(a, cfg):
    a.secret = need_secret(a, cfg)
    a.peer = peer_list(a, cfg)
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
    h = relay_health(a.port)
    if h:
        print(f"relay zaten çalışıyor: label={h['label']} v{h['version']} port={a.port} (pid {read_pid() or '?'}); yeniden başlatmak için: peerlink restart")
        sys.exit(0)
    if getattr(a, "daemon", False):
        # Çocuk süreç için argümanları çözümlenmiş değerlerden kur (sys.argv'yi kopyalama: restart kendine dönerdi).
        # secret komut satırına yazılmaz (ps'te görünmesin); ortam değişkeniyle geçer.
        argv = (["--config", a.config] if a.config else []) + ["up", "--port", str(a.port), "--bind", a.bind,
                "--label", a.label, "--interval", str(a.interval), "--advertise", a.advertise]
        for pr in a.peer: argv += ["--peer", pr]
        os.makedirs(os.path.dirname(PID_PATH), exist_ok=True)
        logf = open(LOG_PATH, "ab")
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__)] + argv, stdout=logf, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True, env={**os.environ, "PEERLINK_SECRET": a.secret})
        for _ in range(30):
            time.sleep(0.2)
            if relay_health(a.port): break
        else:
            print(f"relay başlamadı; log: {LOG_PATH}"); sys.exit(1)
        print(f"relay arka planda: label={a.label} port={a.port} pid={p.pid} log={LOG_PATH}")
        return
    if not a.peer: log("uyarı: hiç eş (--peer) verilmedi; relay gelen mesajları kabul eder ama proxy oluşturmaz")
    os.makedirs(os.path.dirname(PID_PATH), exist_ok=True)
    with open(PID_PATH, "w") as f: f.write(str(os.getpid()))
    try:
        Relay(a).serve()
    finally:
        try: os.unlink(PID_PATH)
        except FileNotFoundError: pass


def cmd_down(a, cfg):
    port = int(resolve(a, cfg, "port", DEFAULT_PORT))
    pid = read_pid()
    if pid and pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
    else:
        # pidfile yok/eski: aynı dosyadan çalışan "up" süreçlerini bul
        me = os.path.abspath(__file__)
        out = subprocess.run(["pgrep", "-f", f"{me} up"], capture_output=True, text=True).stdout.split()
        for x in out:
            if int(x) != os.getpid(): os.kill(int(x), signal.SIGTERM)
        pid = out[0] if out else None
    for _ in range(25):
        if not relay_health(port, 0.5): break
        time.sleep(0.2)
    else:
        print("relay hâlâ cevap veriyor; elle: pkill -f 'relay.py up'"); sys.exit(1)
    try: os.unlink(PID_PATH)
    except FileNotFoundError: pass
    cmd_clean(a, cfg)
    print("relay durdu" if pid else "relay zaten çalışmıyordu")


def cmd_restart(a, cfg):
    cmd_down(a, cfg)
    a.daemon = True
    cmd_up(a, cfg)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="peerlink", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help=f"yapılandırma dosyası (varsayılan {CONFIG_PATH})")
    ap.add_argument("--version", action="version", version=f"peerlink {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--secret"); sp.add_argument("--port", type=int); sp.add_argument("--peer", action="append", default=[])
        return sp

    up = common(sub.add_parser("up", help="relay'i başlat; uzak oturumlar ListAgents'ta görünür"))
    up.add_argument("--bind"); up.add_argument("--advertise", help="bu makinenin LAN IP'si"); up.add_argument("--label"); up.add_argument("--interval", type=float)
    up.add_argument("-d", "--daemon", action="store_true", help=f"arka planda çalıştır (log: {LOG_PATH})")
    common(sub.add_parser("down", help="arka plandaki relay'i durdur ve proxy kayıtlarını temizle"))
    rs = common(sub.add_parser("restart", help="down + up -d (config değişince)"))
    rs.add_argument("--bind"); rs.add_argument("--advertise"); rs.add_argument("--label"); rs.add_argument("--interval", type=float)

    who = common(sub.add_parser("who", help="kim var: relay durumu, yerel oturumlar, proxy'ler, eşler"))
    who.add_argument("--host", help="yalnızca bu uzak makinenin oturumlarını listele")

    send = common(sub.add_parser("send", help="tek seferlik mesaj: peerlink send macB/\"Oturum adı\" \"metin\""))
    send.add_argument("--host", help="etiket yerine doğrudan uzak relay adresi"); send.add_argument("--label")
    send.add_argument("to", help="'etiket/oturum adı' (veya --host ile yalnızca ad)"); send.add_argument("text")

    sub.add_parser("clean", help="ölü proxy kayıtlarını sil")
    common(sub.add_parser("doctor", help="ortamı denetle: python, config, relay, eşler, güvenlik duvarı, ölü proxy'ler"))

    p = sub.add_parser("proxy")
    for f in ("--name", "--remote-host", "--remote-name", "--remote-label", "--local-host", "--label"): p.add_argument(f, required=True)
    p.add_argument("--secret", help="(dahili) normalde PEERLINK_SECRET ortam değişkeniyle gelir")
    p.add_argument("--remote-port", type=int, required=True); p.add_argument("--remote-pid", type=int, required=True)
    p.add_argument("--local-port", type=int, required=True); p.add_argument("--interval", type=float, default=5)

    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    {"up": cmd_up, "down": cmd_down, "restart": cmd_restart, "who": cmd_who, "send": cmd_send, "clean": cmd_clean,
     "doctor": cmd_doctor, "proxy": lambda a, c: cmd_proxy(a)}[a.cmd](a, cfg)


if __name__ == "__main__":
    main()
