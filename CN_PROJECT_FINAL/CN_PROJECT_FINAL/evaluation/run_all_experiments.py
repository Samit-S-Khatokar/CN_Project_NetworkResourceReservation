# run_all_experiments.py - Member C: runs the whole system for real and saves the raw results
#   run 1 reservation mode: E2, E5, E4, E1, scenarios   run 2 baseline mode: E2, E1
#   runs 3 + 4: all core links 10 / 20 Mbps: E3
#   sudo python3 evaluation/run_all_experiments.py [--only e2]   (~7 min), then plot_results.py

import argparse
import copy
import csv
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from mininet.log import setLogLevel
from mininet.clean import cleanup

from config.topology_config import load_topology
from topology.sdn_topology import build_network
from client.client import send_request

RAW = os.path.join(PROJECT_DIR, "results", "raw")
LOGS = os.path.join(PROJECT_DIR, "logs")
CLIENT = os.path.join(PROJECT_DIR, "client", "client.py")
SERVER_IP = "10.0.0.254"
HOST_IPS = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]
TOKENS = {"user1": "demo-token-user1", "user2": "demo-token-user2"}


def log(*args):
    print("[%s]" % time.strftime("%H:%M:%S"), *args, flush=True)


def rest(path):
    with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=3) as resp:
        return json.loads(resp.read())


def wait_for(check, what, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if check():
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("timed out waiting for " + what)


# starting / stopping everything

class System:
    """Controller + Mininet + server for one run."""

    def __init__(self, name, venv, baseline=False, topo_file=None):
        self.name = name
        self.venv = venv
        self.baseline = baseline
        self.topo_file = topo_file or os.path.join(PROJECT_DIR, "config", "topology.json")
        self.env = dict(os.environ, NRRS_BASELINE="1" if baseline else "0", NRRS_TOPOLOGY=self.topo_file,
                        NRRS_UTIL_CSV=os.path.join(RAW, "util_%s.csv" % name), PYTHONUNBUFFERED="1")

    def start(self):
        cleanup()   # same as "sudo mn -c", clears anything left from an old run
        log("== run '%s' (baseline=%s) ==" % (self.name, self.baseline))
        self.controller = subprocess.Popen([os.path.join(self.venv, "bin", "osken-manager"), "controller/sdn_controller.py"],
                                           cwd=PROJECT_DIR, env=self.env,
                                           stdout=open(os.path.join(LOGS, "controller_%s.log" % self.name), "w"),
                                           stderr=subprocess.STDOUT)
        wait_for(lambda: "links" in rest("/links"), "controller REST API")

        self.net, self.root, srv_link = build_network(topo_config=load_topology(self.topo_file))
        self.srv_intf = srv_link.intf1.name
        wait_for(lambda: self.net.get("h1").cmd("ping -c1 -W1 10.0.0.2").count(" 0% packet loss") == 1,
                 "network connectivity", 40)

        self.server = subprocess.Popen([os.path.join(self.venv, "bin", "python"), "-m", "server.reservation_server"],
                                       cwd=PROJECT_DIR, env=self.env,
                                       stdout=open(os.path.join(LOGS, "server_%s.log" % self.name), "w"),
                                       stderr=subprocess.STDOUT)
        wait_for(lambda: send_request("user1", TOKENS["user1"], "LIST user1", "127.0.0.1", 9000).startswith("LIST"),
                 "TCP server")
        for h in self.net.hosts:
            h.cmd("iperf3 -s -D; iperf3 -s -p 5202 -D")
        time.sleep(1)
        log("system up")

    def stop(self):
        for proc in (self.server, self.controller):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        for h in self.net.hosts:
            h.cmd("pkill -x iperf3")
        self.net.stop()
        self.root.cmd("ip link del %s 2>/dev/null" % self.srv_intf)
        log("system stopped")

    def client(self, host, command, user="user1"):
        """run the real client on a Mininet host, like a user would"""
        h = self.net.get(host)
        return h.cmd("python3 %s --host %s --user %s --token %s '%s'" % (CLIENT, SERVER_IP, user, TOKENS[user], command)).strip()


def reserved_flow_count(net):
    # forward rules of reservations have priority 100 (see sdn_controller.py)
    total = 0
    for sw in net.switches:
        total += sw.cmd("ovs-ofctl -O OpenFlow13 dump-flows %s" % sw.name).count("priority=100")
    return total


def parse_iperf_text(text):
    """[  5]   1.00-2.00   sec  1.10 MBytes  9.23 Mbits/sec ... -> [(2.0, 9.23), ...]"""
    points = []
    for m in re.finditer(r"\]\s+([\d.]+)-([\d.]+)\s+sec\s+[\d.]+ \w?Bytes\s+([\d.]+) (\w)bits/sec", text):
        start, end, value, unit = float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(4)
        if end - start > 1.5:       # skip the final summary line
            continue
        scale = {"K": 0.001, "M": 1, "G": 1000}.get(unit, 0.000001)
        points.append((end, value * scale))
    return points


# E2: guarantee under congestion

def experiment_e2(system, mode, duration=30, flood_start=10, flood_len=12):
    log("E2: 5 Mbps reserved h1->h2, UDP flood h3->h4 (30 Mbps) from t=%ds to t=%ds" % (flood_start, flood_start + flood_len))
    net = system.net
    h1, h3 = net.get("h1", "h3")
    reply = system.client("h1", "REQUEST user1 10.0.0.1 10.0.0.2 5 %d" % (duration + 20))
    log("   reservation:", reply)
    rid = reply.split()[1] if reply.startswith("ACCEPT") else None

    ping_file = os.path.join(RAW, "e2_ping_%s.txt" % mode)
    h1.cmd("ping -D -i 0.2 -w %d 10.0.0.2 > %s 2>&1 &" % (duration, ping_file))
    h3.cmd("(sleep %d; iperf3 -c 10.0.0.4 -p 5202 -u -b 30M -t %d) > /dev/null 2>&1 &" % (flood_start, flood_len))
    out = h1.cmd("timeout %d iperf3 -c 10.0.0.2 -t %d -i 1 -f m --forceflush 2>&1" % (duration + 15, duration))
    with open(os.path.join(RAW, "e2_iperf_%s.txt" % mode), "w") as f:
        f.write(out)

    points = parse_iperf_text(out)
    with open(os.path.join(RAW, "e2_throughput_%s.csv" % mode), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "mbps", "flood_on"])
        got = {round(t): v for t, v in points}
        for t in range(1, duration + 1):
            # seconds with no iperf line = the TCP connection was stuck, count as 0
            w.writerow([t, round(got.get(t, 0.0), 3), int(flood_start < t <= flood_start + flood_len)])
    time.sleep(2)
    if rid:
        system.client("h1", "CANCEL " + rid)
    log("   avg during flood: %.2f Mbps" % avg([v for t, v in points if flood_start < t <= flood_start + flood_len]))


def avg(values):
    return sum(values) / len(values) if values else 0.0


# E5: link failure

def experiment_e5(system, duration=30, fail_at=10):
    log("E5: 5 Mbps reserved h1->h2 on Path A, link s1-s2 goes down at t=%ds" % fail_at)
    net = system.net
    h1 = net.get("h1")
    reply = system.client("h1", "REQUEST user1 10.0.0.1 10.0.0.2 5 %d" % (duration + 20))
    log("   reservation:", reply)
    rid = reply.split()[1]
    path_before = reply.split()[2]

    out_file = os.path.join(RAW, "e5_iperf.txt")
    h1.cmd("timeout %d iperf3 -c 10.0.0.2 -t %d -i 0.5 -f m --forceflush > %s 2>&1 &" % (duration + 15, duration, out_file))
    iperf_start = time.time()
    time.sleep(fail_at)

    fail_time = time.time()
    net.configLinkStatus("s1", "s2", "down")
    rerouted_after = None
    while time.time() - fail_time < 10:
        res = [r for r in rest("/stats")["active_reservations"] if r["id"] == int(rid)]
        if res and "s3" in res[0]["path"]:
            rerouted_after = time.time() - fail_time
            break
        time.sleep(0.05)
    status_after = system.client("h1", "STATUS " + rid)

    time.sleep(max(0, duration + 2 - (time.time() - iperf_start)))
    net.configLinkStatus("s1", "s2", "up")
    with open(out_file) as f:
        points = parse_iperf_text(f.read())

    # recovery = from the failure until throughput is back above 80% of the 5 Mbps
    recovered_at = None
    for t, v in points:
        if t > fail_at + 0.5 and v >= 4.0:
            recovered_at = t
            break
    lost = [v for t, v in points if fail_at < t <= (recovered_at or fail_at)]
    info = {
        "path_before": path_before,
        "status_after": status_after,
        "fail_at_s": fail_at,
        "controller_rerouted_after_s": round(rerouted_after, 3) if rerouted_after else None,
        "throughput_recovered_at_s": recovered_at,
        "recovery_time_s": round(recovered_at - fail_at, 2) if recovered_at else None,
        "avg_mbps_before_failure": round(avg([v for t, v in points if 2 < t <= fail_at]), 3),
        "avg_mbps_after_recovery": round(avg([v for t, v in points if recovered_at and t > recovered_at]), 3),
        "survived": status_after.startswith("STATUS ACTIVE") and "s3" in status_after,
    }
    info["approx_data_lost_mbit"] = round(sum(max(0, info["avg_mbps_before_failure"] - v) * 0.5 for v in lost), 2)
    with open(os.path.join(RAW, "e5_link_failure.json"), "w") as f:
        json.dump(info, f, indent=2)
    log("   ", info)
    system.client("h1", "CANCEL " + rid)


# E4: expiry

def experiment_e4(system, waves_at=(0, 1, 4, 8), total_time=13):
    log("E4: waves of 3-second reservations, checking rules and bandwidth are freed")
    net = system.net
    pairs = [(a, b) for a in HOST_IPS for b in HOST_IPS if a != b]
    timeline, waves = [], []
    start = time.time()
    next_wave = 0
    while time.time() - start < total_time:
        t = time.time() - start
        if next_wave < len(waves_at) and t >= waves_at[next_wave]:
            acc = rej = 0
            for src, dst in pairs:
                reply = send_request("user1", TOKENS["user1"], "REQUEST user1 %s %s 8 3" % (src, dst), "127.0.0.1", 9000)
                if reply.startswith("ACCEPT"):
                    acc += 1
                else:
                    rej += 1
            waves.append({"wave": next_wave, "fired_at_s": round(t, 2), "accepted": acc, "rejected": rej})
            next_wave += 1
        active = rest("/stats")["active_reservations"]
        timeline.append({"elapsed_s": round(time.time() - start, 2),
                         "reserved_mbps": sum(r["mbps"] for r in active),
                         "active_reservations": len(active),
                         "reserved_rules_in_switches": reserved_flow_count(net)})
        time.sleep(0.25)
    write_rows(os.path.join(RAW, "e4_timeline.csv"), timeline)
    write_rows(os.path.join(RAW, "e4_waves.csv"), waves)
    last = timeline[-1]
    log("   waves:", waves)
    log("   at the end: %d reservations, %d rules left -> %s" % (
        last["active_reservations"], last["reserved_rules_in_switches"],
        "PASS" if last["active_reservations"] == 0 and last["reserved_rules_in_switches"] == 0 else "FAIL"))


# E1 / E3: many users sending random requests at the same time

def load_round(label, users, seconds, rows, seed):
    stop_at = time.time() + seconds

    def user_loop(n):
        rnd = random.Random(seed * 1000 + n)
        user = "user1" if n % 2 == 0 else "user2"
        while time.time() < stop_at:
            src, dst = rnd.sample(HOST_IPS, 2)
            mbps, dur = rnd.randint(2, 10), rnd.randint(2, 6)
            t0 = time.time()
            try:
                reply = send_request(user, TOKENS[user], "REQUEST %s %s %s %d %d" % (user, src, dst, mbps, dur),
                                     "127.0.0.1", 9000)
            except Exception as e:
                reply = "ERROR " + type(e).__name__
            parts = reply.split()
            rows.append([label, users, n, round(t0, 3), src, dst, mbps, dur, parts[0],
                         parts[1] if parts[0] != "ACCEPT" and len(parts) > 1 else "",
                         round((time.time() - t0) * 1000, 2)])
            time.sleep(rnd.uniform(1, 3))

    threads = [threading.Thread(target=user_loop, args=(n,)) for n in range(users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def experiment_e1(mode, seconds=15):
    log("E1: 5/10/20/40 users sending random requests (%ds each)" % seconds)
    rows, rounds = [], []
    for users in (5, 10, 20, 40):
        start = time.time()
        load_round(mode, users, seconds, rows, seed=users)
        rounds.append({"mode": mode, "users": users, "start": round(start, 3), "end": round(time.time(), 3)})
        done = [r for r in rows if r[1] == users]
        log("   %2d users: %d requests, %d accepted" % (users, len(done), sum(1 for r in done if r[8] == "ACCEPT")))
        time.sleep(7)    # all reservations (max 6 s) expire before the next round
    write_rows(os.path.join(RAW, "e1_requests_%s.csv" % mode), rows,
               ["mode", "users", "user_no", "time", "src", "dst", "mbps", "seconds", "result", "reason", "reply_ms"])
    write_rows(os.path.join(RAW, "e1_rounds_%s.csv" % mode), rounds)


def experiment_e3(label, seconds=30):
    log("E3: 20 users for %ds on '%s'" % (seconds, label))
    rows = []
    start = time.time()
    load_round(label, 20, seconds, rows, seed=3)
    write_rows(os.path.join(RAW, "e3_requests_%s.csv" % label), rows,
               ["run", "users", "user_no", "time", "src", "dst", "mbps", "seconds", "result", "reason", "reply_ms"])
    write_rows(os.path.join(RAW, "e3_window_%s.csv" % label), [{"run": label, "start": round(start, 3), "end": round(time.time(), 3)}])
    time.sleep(7)


# robustness scenarios (A)

def scenarios(system):
    log("Scenario: server overload (50 clients at once)")
    out = subprocess.run([sys.executable, "tests/load_test.py", "50"], cwd=PROJECT_DIR,
                         capture_output=True, text=True).stdout
    summary = out[out.find("=== Load Test Results ==="):]
    log("   " + summary.replace("\n", " | "))
    log("Scenario: unauthorized user")
    bad_token = system.net.get("h3").cmd("python3 %s --host %s --user user1 --token wrong '%s'"
                                         % (CLIENT, SERVER_IP, "REQUEST user1 10.0.0.3 10.0.0.4 5 30")).strip()
    no_user = system.net.get("h3").cmd("python3 %s --host %s --user mallory --token x '%s'"
                                       % (CLIENT, SERVER_IP, "REQUEST mallory 10.0.0.3 10.0.0.4 5 30")).strip()
    log("   wrong token ->", bad_token, "| unknown user ->", no_user)
    with open(os.path.join(RAW, "scenarios.txt"), "w") as f:
        f.write(summary + "\nUnauthorized: wrong token -> %s\nUnauthorized: unknown user -> %s\n" % (bad_token, no_user))


def write_rows(path, rows, header=None):
    with open(path, "w", newline="") as f:
        if rows and isinstance(rows[0], dict):
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        else:
            w = csv.writer(f)
            if header:
                w.writerow(header)
            w.writerows(rows)


def capacity_variant(core_bw):
    """copy of the topology with every switch-switch link set to core_bw (for E3)"""
    topo = copy.deepcopy(load_topology())
    for link in topo["links"]:
        if link["a"] in topo["switches"] and link["b"] in topo["switches"]:
            link["bw"] = core_bw
    path = os.path.join(RAW, "topology_core%d.json" % core_bw)
    with open(path, "w") as f:
        json.dump(topo, f, indent=2)
    return path


def run(name, venv, baseline=False, topo_file=None, steps=()):
    system = System(name, venv, baseline, topo_file)
    system.start()
    try:
        for step in steps:
            step(system)
    finally:
        system.stop()


def give_files_back():
    # we ran with sudo, so results/ and logs/ now belong to root. Hand them back
    # to the normal user, otherwise plot_results.py / run_server.sh can't write there.
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:
        for folder in (os.path.join(PROJECT_DIR, "results"), LOGS):
            subprocess.run(["chown", "-R", "%s:%s" % (uid, gid), folder])


def main():
    parser = argparse.ArgumentParser()
    default_venv = os.path.join(os.path.expanduser("~" + os.environ.get("SUDO_USER", "")), "nrrs-env")
    parser.add_argument("--venv", default=default_venv, help="virtualenv with os-ken (default ~/nrrs-env)")
    parser.add_argument("--only", choices=["e1", "e2", "e3", "e4", "e5", "scenarios"])
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Mininet needs root:  sudo python3 evaluation/run_all_experiments.py")
    if not os.path.exists(os.path.join(args.venv, "bin", "osken-manager")):
        sys.exit("osken-manager not found in %s - create the venv first (see README) or pass --venv" % args.venv)

    os.makedirs(RAW, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    setLogLevel("critical")     # hide Mininet's chatter (and the harmless sch_htb warnings)
    want = lambda e: args.only in (None, e)
    t0 = time.time()
    try:
        run_everything(args, want)
    finally:
        give_files_back()
    log("all done in %.1f min. Now:  ~/nrrs-env/bin/python evaluation/plot_results.py" % ((time.time() - t0) / 60))


def run_everything(args, want):
    # E1's numbers are worked out from util_reservation.csv / util_baseline.csv, so a
    # run of just one other experiment writes its own file instead of overwriting those
    suffix = "" if args.only in (None, "e1") else "_" + args.only
    steps = []
    if want("e2"):
        steps.append(lambda s: experiment_e2(s, "reservation"))
    if want("e5"):
        steps.append(lambda s: experiment_e5(s))
    if want("e4"):
        steps.append(lambda s: (time.sleep(3), experiment_e4(s)))
    if want("e1"):
        steps.append(lambda s: (time.sleep(5), experiment_e1("reservation")))
    if want("scenarios"):
        steps.append(lambda s: (time.sleep(7), scenarios(s)))
    if steps:
        run("reservation" + suffix, args.venv, steps=steps)

    steps = []
    if want("e2"):
        steps.append(lambda s: experiment_e2(s, "baseline"))
    if want("e1"):
        steps.append(lambda s: (time.sleep(5), experiment_e1("baseline")))
    if steps:
        run("baseline" + suffix, args.venv, baseline=True, steps=steps)

    if want("e3"):
        for bw in (10, 20):
            label = "core%d" % bw
            run(label, args.venv, topo_file=capacity_variant(bw), steps=[lambda s, l=label: experiment_e3(l)])


if __name__ == "__main__":
    main()
