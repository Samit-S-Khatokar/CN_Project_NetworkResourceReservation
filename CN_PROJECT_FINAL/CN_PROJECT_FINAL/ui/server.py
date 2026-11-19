# server.py - simple web interface with a 3D view of the live network
# Starts the controller, Mininet and the TCP server, then serves the page on port 8000.
#   bash ui.sh      (same as: sudo python3 ui/server.py)
import json
import os
import re
import shlex
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from mininet.log import setLogLevel

from config.topology_config import load_topology
from evaluation.run_all_experiments import System, rest, give_files_back, RAW, LOGS, TOKENS, SERVER_IP

UI_DIR = os.path.dirname(os.path.abspath(__file__))
FILES = {"/": ("index.html", "text/html"), "/three.min.js": ("three.min.js", "application/javascript"),
         "/OrbitControls.js": ("OrbitControls.js", "application/javascript")}
TOPO = load_topology()
HOSTS = {name: info["ip"] for name, info in TOPO["hosts"].items()}
NAME = {ip: name for name, ip in HOSTS.items()}
CORE = {tuple(sorted((l["a"], l["b"]))): l["bw"] for l in TOPO["links"]
        if l["a"] in TOPO["switches"] and l["b"] in TOPO["switches"]}

system = None
owners = {}                       # booking id -> (end time, user), for the countdown and cancel
busy = threading.Lock()           # one action at a time: Mininet host shells are not shared safely


def client(host, user, message):
    """the real client.py inside a Mininet host; returns the conversation and the last reply"""
    out = system.net.get(host).cmd("cd %s && python3 client/client.py --show --host %s --user %s --token %s %s" % (
        PROJECT_DIR, SERVER_IP, user, TOKENS[user], shlex.quote(message))).replace("\r", "")
    lines = [l for l in out.splitlines() if l.startswith(("client -> ", "server <- "))]
    reply = lines[-1][10:] if lines else out.strip()
    p = reply.split()
    if len(p) >= 4 and p[0] == "ACCEPT":
        owners[int(p[1])] = (float(p[3]), user)
    return {"lines": lines or [out.strip()], "reply": reply}


def book(d):
    src, dst, user = d.get("src"), d.get("dst"), d.get("user", "user1")
    if src not in HOSTS or dst not in HOSTS or user not in TOKENS:
        raise ValueError("pick two computers")
    return client(src, user, "REQUEST %s %s %s %s %s" % (user, HOSTS[src], HOSTS[dst],
                                                        float(d.get("mbps", 5)), int(float(d.get("secs", 120)))))


def cancel(d):
    rid = int(d["id"])
    return client("h1", owners.get(rid, (0, "user1"))[1], "CANCEL %d" % rid)


def link(d):
    a, b = d["link"].split("-")
    if tuple(sorted((a, b))) not in CORE:
        raise ValueError("unknown link")
    system.net.configLinkStatus(a, b, "up" if d.get("up") else "down")
    time.sleep(1.5)                 # let the controller and manager react
    return {"lines": [], "reply": "link %s-%s is %s" % (a, b, "UP" if d.get("up") else "DOWN")}


def traffic(d):
    src, dst = d.get("src"), d.get("dst")
    if src not in HOSTS or dst not in HOSTS:
        raise ValueError("pick two computers")
    out = system.net.get(src).cmd("timeout 20 iperf3 -c %s -t 4 -f m" % HOSTS[dst])
    m = re.search(r"([\d.]+) Mbits/sec.*receiver", out)
    return {"lines": [], "reply": "%s -> %s measured %s Mbps" % (src, dst, m.group(1) if m else "0")}


ACTIONS = {"book": book, "cancel": cancel, "link": link, "traffic": traffic}


def state():
    stats, links = rest("/stats"), rest("/links")["links"]
    rates = stats.get("link_rates_mbps", {})
    up = {tuple(sorted((l["a"], l["b"]))): l["up"] for l in links}
    booked = {}
    for r in stats["active_reservations"]:
        for hop in zip(r["path"][:-1], r["path"][1:]):
            booked[hop] = booked.get(hop, 0) + r["mbps"]
    result = {}
    for (a, b), cap in CORE.items():
        result["%s-%s" % (a, b)] = {"cap": cap, "up": up.get((a, b), True),
                                    "booked": max(booked.get((a, b), 0), booked.get((b, a), 0)),
                                    "measured": max(rates.get("%s->%s" % (a, b), 0), rates.get("%s->%s" % (b, a), 0))}
    res = []
    for r in stats["active_reservations"]:
        end, user = owners.get(r["id"], (None, "?"))
        res.append({"id": r["id"], "from": NAME.get(r["src_ip"]), "to": NAME.get(r["dst_ip"]), "mbps": r["mbps"],
                    "path": r["path"], "user": user, "left": None if end is None else max(0, round(end - time.time()))})
    return {"links": result, "bookings": sorted(res, key=lambda r: r["id"])}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in FILES:
            name, kind = FILES[self.path]
            with open(os.path.join(UI_DIR, name), "rb") as f:
                return self.send(200, f.read(), kind)
        if self.path == "/api/state":
            try:
                return self.send(200, state())
            except Exception as e:
                return self.send(503, {"error": str(e)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        action = ACTIONS.get(self.path.rsplit("/", 1)[-1])
        if action is None:
            return self.send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        with busy:
            try:
                self.send(200, action(body))
            except (ValueError, KeyError, TypeError) as e:
                self.send(400, {"lines": [], "reply": "ERROR %s" % e})

    def log_message(self, fmt, *args):
        pass


def stop(signum, frame):
    raise KeyboardInterrupt


def main():
    global system
    venv = os.path.join(os.path.expanduser("~" + os.environ.get("SUDO_USER", "")), "nrrs-env")
    if len(sys.argv) > 2 and sys.argv[1] == "--venv":
        venv = sys.argv[2]
    if os.geteuid() != 0:
        sys.exit("Mininet needs root:  sudo python3 ui/server.py")
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    setLogLevel("critical")
    print("Starting the controller, the Mininet network and the TCP server (about 15 s)...")
    system = System("ui", venv)
    web = None
    try:
        system.start()
        web = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
        signal.signal(signal.SIGTERM, stop)
        print("\n  Open http://localhost:8000 in your browser. Ctrl+C here to stop.\n", flush=True)
        web.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping...")
        if web:
            web.server_close()
        try:
            system.stop()
        except AttributeError:          # startup failed before everything was running
            pass
        give_files_back()


if __name__ == "__main__":
    main()
