"""İki sahte oturum (LAN-PROBE, LAN-PROBE-2) arasında relay üzerinden gidiş-dönüş testi.
Gerçek oturumlar kullanılmaz; çerçeveler doğrudan proxy socket'lerine yazılır."""
import json, glob, os, socket, hashlib, time, uuid, sys
SESS = os.path.expanduser('~/.claude/sessions')
def regs():
    out = []
    for p in glob.glob(f'{SESS}/*.json'):
        try: out.append(json.load(open(p)))
        except Exception: pass
    return out
def by_name(n): return next(r for r in regs() if r.get('name') == n)
def token(r): return json.load(open(f"{SESS}/{r['pid']}.{hashlib.sha256(r['messagingSocketPath'].encode()).hexdigest()}.key"))['peerToken']
def send(target, sender, text):
    content = f'<cross-session-message from="uds:{sender["messagingSocketPath"]}" from-session="x" from-name="{sender["name"]}" from-mode="prompting">\n{text}\n</cross-session-message>'
    frame = {"msgV":1,"msg_id":str(uuid.uuid4()),"type":"user","message":{"role":"user","content":content},"priority":"next","from":f'uds:{sender["messagingSocketPath"]}'}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(target['messagingSocketPath'])
    s.sendall((json.dumps({"type":"auth","token":token(target)})+"\n"+json.dumps(frame)+"\n").encode()); s.close()
p1, p2 = by_name('LAN-PROBE'), by_name('LAN-PROBE-2')
# 1) LAN-PROBE -> remote/LAN-PROBE-2 (proxy) -> relay -> LAN-PROBE-2 (gerçek socket)
send(by_name('remote/LAN-PROBE-2'), p1, 'RT-1: LAN-PROBE -> LAN-PROBE-2')
time.sleep(1.5)
# 2) LAN-PROBE-2 "cevap verir": relay'in LAN-PROBE-2'ye teslim ettiği çerçevedeki from yolunu kullanır
last = [l for l in open(os.environ.get('PROBE_LOG', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'probe.log'))) if 'RT-1' in l][-1]
import re; reply_to = re.search(r'from=\\\\"uds:([^\\\\]+)', last).group(1)
print('LAN-PROBE-2 gördüğü cevap adresi:', reply_to, '-> proxy adı:', next(r['name'] for r in regs() if r['messagingSocketPath']==reply_to))
tgt = next(r for r in regs() if r['messagingSocketPath']==reply_to)
send(tgt, p2, 'RT-2: LAN-PROBE-2 -> LAN-PROBE (cevap)')
time.sleep(1.5)
last = [l for l in open(os.environ.get('PROBE_LOG', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'probe.log'))) if 'RT-2' in l][-1]
got = re.search(r'from=\\\\"uds:([^\\\\]+)', last).group(1)
print('LAN-PROBE aldığı cevabın from adresi:', got, '-> proxy adı:', next(r['name'] for r in regs() if r['messagingSocketPath']==got))
