# terminal_demo.py - the whole demo in the terminal: starts the controller, Mininet and the
# TCP server, then runs 13 steps, each with the real command, its output and what to say.
#   bash demo.sh      (same as: sudo python3 demo/terminal_demo.py)

import argparse
import os
import re
import shlex
import subprocess
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from mininet.log import setLogLevel

from evaluation.run_all_experiments import System, give_files_back, RAW, LOGS, TOKENS, CLIENT, SERVER_IP

HOSTS = {"h1": "10.0.0.1", "h2": "10.0.0.2", "h3": "10.0.0.3", "h4": "10.0.0.4"}
UTIL_CSV = os.path.join(RAW, "util_demo.csv")
SERVER_LOG = os.path.join(LOGS, "server.log")
CONTROLLER_LOG = os.path.join(LOGS, "controller_demo.log")

BOLD, DIM, PURPLE, ORANGE, GREEN, RED, END = ("\033[1m", "\033[2m", "\033[95m", "\033[33m",
                                              "\033[92m", "\033[91m", "\033[0m")

system = None
log_start = 0


# printing

def header(n, title, req=""):
    tag = "   [%s]" % req if req else ""
    print("\n" + PURPLE + BOLD + "=" * 72)
    print(" STEP %s  %s%s" % (n, title, tag))
    print("=" * 72 + END)


def show(prompt, command, output):
    """print a command the way you'd type it, then its real output"""
    print(BOLD + ORANGE + "%s %s" % (prompt, command) + END)
    for line in output.replace("\r", "").strip("\n").rstrip().splitlines():
        print("   " + line)


def say(text):
    print("\n" + GREEN + BOLD + "SAY: " + END + GREEN + text + END)


def note(text):
    print(DIM + "   (" + text + ")" + END)


def pause(text="Press Enter for the next step (q + Enter to stop) "):
    try:
        answer = input("\n" + DIM + text + END)
    except EOFError:
        answer = ""
    if answer.strip().lower() == "q":
        raise KeyboardInterrupt


# doing things on the network

def on(host, command):
    """run a command on a Mininet host and show it like the mininet> prompt does"""
    output = system.net.get(host).cmd(command)
    show("mininet> " + host, command, output)
    return output


def sh(command):
    """run a command on the laptop itself (like 'sh ...' at the mininet> prompt)"""
    output = subprocess.run(command, shell=True, cwd=PROJECT_DIR, capture_output=True, text=True).stdout
    show("mininet> sh", command, output)
    return output


def client(host, message, user="user1", token=None):
    """the real client.py, run inside a Mininet host, with the whole conversation shown"""
    token = token or TOKENS[user]
    command = "python3 client/client.py --show --host %s --user %s --token %s %s" % (
        SERVER_IP, user, token, shlex.quote(message))
    output = system.net.get(host).cmd("cd %s && %s" % (PROJECT_DIR, command))
    shown = command.replace("--token " + token + " ", "--token %s " % ("****" if token == TOKENS[user] else token))
    show("mininet> " + host, shown, output)
    replies = [l[len("server <- "):] for l in output.splitlines() if l.startswith("server <- ")]
    return replies[-1].strip() if replies else output.strip()


def booking_rules(rid):
    return sum(sw.cmd("ovs-ofctl -O OpenFlow13 dump-flows %s cookie=%d/-1" % (sw.name, rid)).count("cookie=")
               for sw in system.net.switches)


def iperf_summary(text):
    keep = [l for l in text.splitlines() if "sender" in l or "receiver" in l]
    return "\n".join(keep) or text.strip()


def last_util_rows(links=("s1->s2", "s2->s4", "s1->s3", "s3->s4")):
    """header + the newest row for each core link from the utilization CSV"""
    with open(UTIL_CSV) as f:
        lines = f.read().splitlines()
    rows = {}
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) > 2 and parts[2] in links:
            rows[parts[2]] = line
    return "\n".join([lines[0]] + [rows[l] for l in links if l in rows])


# the demo, step by step

def demo():
    header(0, "What is running")
    with open(CONTROLLER_LOG) as f:
        connected = sorted(set(re.findall(r"Switch connected: (\d+)", f.read())))
    show("tab 1", "SDN controller (OS-Ken, OpenFlow 1.3, port 6653, REST 8080)",
         "\n".join("Switch connected: %s" % s for s in connected))
    show("tab 2", "TCP reservation server + manager", "listening on port 9000 (10.0.0.254 from the hosts)")
    say("The SDN controller runs OS-Ken with OpenFlow 1.3, and switches s1 to s4 are connected to it. "
        "Our TCP server listens on port 9000, with the reservation manager inside it.")
    pause()

    header(1, "The network")
    lines, lost, total = [], 0, 0
    for a in HOSTS:
        row = []
        for b, ip in HOSTS.items():
            if a == b:
                continue
            ok = " 0% packet loss" in system.net.get(a).cmd("ping -c1 -W1 %s" % ip)
            row.append(b if ok else "X")
            total += 1
            lost += 0 if ok else 1
        lines.append("%s -> %s" % (a, " ".join(row)))
    lines.append("*** Results: %d%% dropped (%d/%d received)" % (100 * lost // total, total - lost, total))
    show("mininet>", "pingall", "\n".join(lines))
    say("Mininet built 4 hosts and 4 switches. h1 and h3 are on s1, h2 and h4 on s4. "
        "Path A through s2 is 10 Mbps, Path B through s3 is 20 Mbps. Everyone can reach everyone.")
    pause()

    header(2, "Book 5 Mbps from h1 to h2", "R1 TCP protocol")
    reply = client("h1", "REQUEST user1 10.0.0.1 10.0.0.2 5 300")
    if not reply.startswith("ACCEPT"):
        print(RED + "Expected ACCEPT, got: " + reply + END)
        return
    rid = int(reply.split()[1])
    with open(CONTROLLER_LOG) as f:
        installed = [l for l in f.read().splitlines() if "installed reservation %d " % rid in l]
    show("tab 1", "(controller log)", installed[-1] if installed else "")
    say("h1 opens a TCP connection to our server, logs in with AUTH, and asks for 5 Mbps to h2 for 300 seconds. "
        "The manager checks every link on Path A, finds room and says yes. The controller installs the rules.")
    pause()

    header(3, "The SDN rules inside switch s1", "R5 SDN policies")
    sh("ovs-ofctl -O OpenFlow13 dump-flows s1 cookie=%d/-1" % rid)
    sh("ovs-ofctl -O OpenFlow13 dump-meters s1 | grep -A1 'meter=%d '" % rid)
    say("These are booking %d's rules in s1. cookie=%#x is the booking number. Priority 100 is the booked "
        "direction and it goes through meter %d, a 5000 kbps speed limit. Priority 90 is for replies. "
        "n_packets is 0: making a booking sends no data, it only reserves." % (rid, rid, rid))
    pause()

    header(4, "Send real traffic h1 -> h2 and measure it", "R5")
    note("iperf3 servers are already running on every host")
    out = system.net.get("h1").cmd("iperf3 -c 10.0.0.2 -t 5 -f m")
    show("mininet> h1", "iperf3 -c 10.0.0.2 -t 5", iperf_summary(out))
    say("iperf sends real data from h1 to h2 and measures the speed. The link can carry 10 Mbps, "
        "but the meter holds this flow at its booking: about 5 Mbps.")
    pause()

    header(5, "Did those packets use our rule?", "R5")
    sh("ovs-ofctl -O OpenFlow13 dump-flows s1 cookie=%d/-1" % rid)
    say("n_packets went from 0 to thousands. Those were iperf's packets, matched to our booking's rule.")
    pause()

    header(6, "Congestion: h3 floods the same link with 30 Mbps (no booking)", "guarantee")
    system.net.get("h3").cmd("iperf3 -c 10.0.0.4 -p 5202 -u -b 30M -t 12 > /dev/null 2>&1 &")
    show("mininet> h3", "iperf3 -c 10.0.0.4 -p 5202 -u -b 30M -t 12 &", "UDP flood running in the background")
    time.sleep(2)
    out = system.net.get("h1").cmd("iperf3 -c 10.0.0.2 -t 5 -f m")
    show("mininet> h1", "iperf3 -c 10.0.0.2 -t 5", iperf_summary(out))
    say("h3 floods the same link with 30 Mbps and has no booking. Our booked flow still gets its 5 Mbps, "
        "because traffic without a booking can only use what is left over.")
    time.sleep(6)          # let the flood finish before the next step
    pause()

    header(7, "Requests the manager refuses", "R4 conflicts")
    client("h1", "REQUEST user1 10.0.0.1 10.0.0.2 5 300")
    note("same pair again")
    client("h3", "REQUEST user1 10.0.0.3 10.0.0.4 25 60")
    note("25 Mbps fits on no path: Path A has 5 left, Path B is 20")
    client("h1", "LIST user1", token="wrong-token")
    note("wrong login")
    say("Same pair twice is a CONFLICT. 25 Mbps fits on no path, so NO_CAPACITY. "
        "A wrong token is refused before anything else: UNAUTHORIZED.")
    pause()

    header(8, "What the manager knows", "R3 resources, R7 utilization")
    client("h1", "STATUS %d" % rid)
    show("mininet> sh", "tail results/raw/util_demo.csv   (newest row per core link)", last_util_rows())
    say("The manager keeps every booking: speed, path, end time and owner. Every second it also saves, "
        "for each link, how much is booked and how much traffic the switches really counted.")
    pause()

    header(9, "A link breaks", "rerouting")
    system.net.configLinkStatus("s1", "s2", "down")
    show("mininet>", "link s1 s2 down", "link s1-s2 is DOWN")
    time.sleep(2)
    client("h1", "STATUS %d" % rid)
    system.net.configLinkStatus("s1", "s2", "up")
    show("mininet>", "link s1 s2 up", "link s1-s2 is UP again")
    time.sleep(2)
    say("I cut the cable between s1 and s2. The switch tells the controller, and the manager moves "
        "the booking to Path B through s3, which has room.")
    pause()

    header(10, "Expiry: a 10-second booking", "R6 expiry")
    reply = client("h3", "REQUEST user1 10.0.0.3 10.0.0.4 3 10")
    if reply.startswith("ACCEPT"):
        short = int(reply.split()[1])
        note("rules for booking %d in the switches now: %d" % (short, booking_rules(short)))
        for left in range(12, 0, -1):
            print("\r   waiting for it to expire... %2d s " % left, end="", flush=True)
            time.sleep(1)
        print()
        client("h3", "STATUS %d" % short)
        note("rules for booking %d now: %d" % (short, booking_rules(short)))
    say("Every booking has a time limit. A timer checks every half second, frees the bandwidth "
        "and tells the controller to delete the rules.")
    pause()

    header(11, "Cancel early", "R1")
    client("h1", "CANCEL %d" % rid)
    client("h1", "LIST user1")
    say("Users can also end a booking early. Now user1 has nothing left.")
    pause()

    header(12, "50 clients at the same moment", "R2 concurrency")
    out = subprocess.run([sys.executable, "tests/load_test.py", "50"], cwd=PROJECT_DIR,
                         capture_output=True, text=True).stdout
    summary = out[out.find("=== Load Test Results"):] if "=== Load Test Results" in out else out
    show("mininet> sh", "python3 tests/load_test.py 50", summary)
    say("50 clients connect at the same moment. Each gets its own thread on the server, "
        "and one lock in the manager makes sure two of them can never book the same bandwidth.")
    pause()

    header(13, "Accepted vs rejected", "R8 evaluation")
    counts = {}
    with open(SERVER_LOG) as f:
        f.seek(log_start)
        for line in f:
            m = re.search(r"request='REQUEST[^']*' response='(\w+)(?: (\w+))?", line)
            if m:
                key = "ACCEPT" if m.group(1) == "ACCEPT" else "REJECT " + (m.group(2) or "")
                counts[key] = counts.get(key, 0) + 1
    show("mininet> sh", "count REQUEST replies in logs/server.log (this demo)",
         "\n".join("%5d  %s" % (v, k) for k, v in sorted(counts.items(), key=lambda kv: (kv[0] != "ACCEPT", kv[0]))))
    e2 = os.path.join(PROJECT_DIR, "results", "e2_congestion.csv")
    if os.path.exists(e2):
        with open(e2) as f:
            show("results", "e2_congestion.csv (5 Mbps booked, 30 Mbps flood)", f.read())
    graphs = os.path.join(PROJECT_DIR, "results", "graphs")
    if os.path.isdir(graphs):
        show("results", "graphs/", "\n".join(sorted(os.listdir(graphs))))
    say("Every request is logged, so we can count yes and no. In our experiments a booked flow kept "
        "4.6 Mbps under a 30 Mbps flood; without our system it got 0.26 Mbps. The graphs are in results/graphs.")
    print("\n" + PURPLE + BOLD + "Demo finished." + END)
    pause("Press Enter to stop everything ")


def main():
    global system, log_start
    parser = argparse.ArgumentParser()
    default_venv = os.path.join(os.path.expanduser("~" + os.environ.get("SUDO_USER", "")), "nrrs-env")
    parser.add_argument("--venv", default=default_venv)
    args = parser.parse_args()
    if os.geteuid() != 0:
        sys.exit("Mininet needs root:  sudo python3 demo/terminal_demo.py")
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    setLogLevel("critical")          # hides Mininet's harmless sch_htb warnings

    print(BOLD + "Starting the controller, the Mininet network and the TCP server (about 15 s)..." + END)
    system = System("demo", args.venv)
    try:
        system.start()
        log_start = os.path.getsize(SERVER_LOG) if os.path.exists(SERVER_LOG) else 0
        demo()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        try:
            system.stop()
        except Exception:
            pass
        give_files_back()
        print("Everything stopped.")


if __name__ == "__main__":
    main()
