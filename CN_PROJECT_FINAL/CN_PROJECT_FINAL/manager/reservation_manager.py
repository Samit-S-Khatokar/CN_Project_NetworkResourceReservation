# reservation_manager.py - Member C
# Keeps the booking table and how much of every link is booked, decides accept / reject,
# expires old bookings, reroutes on link failure, and tells Member B's controller what to install.
# Links are full duplex, so each link is stored twice (s1->s2 and s2->s1) with its own capacity.
import json
import logging
import threading
import time

import networkx as nx

log = logging.getLogger("manager")

NO_CAPACITY, CONFLICT, INVALID = "NO_CAPACITY", "CONFLICT", "INVALID"
UNAUTHORIZED, CONTROLLER_ERROR = "UNAUTHORIZED", "CONTROLLER_ERROR"


class Reservation:
    def __init__(self, res_id, user, src_ip, dst_ip, mbps, end_time, path):
        self.res_id, self.user, self.src_ip, self.dst_ip = res_id, user, src_ip, dst_ip
        self.mbps, self.end_time, self.path = mbps, end_time, path   # path like ['h1','s1','s2','s4','h2']
        self.status = "PENDING"                                       # -> ACTIVE -> EXPIRED / CANCELLED / FAILED


class ReservationManager:
    def __init__(self, topology_file, controller, baseline_mode=False, max_paths_to_try=3, expiry_interval=0.5):
        self.controller = controller              # RyuClient (real) or MockController (tests)
        self.baseline_mode = baseline_mode        # True = accept everything, for comparison
        self.max_paths_to_try = max_paths_to_try
        self.expiry_interval = expiry_interval
        self.graph = nx.DiGraph()                 # edge data: cap (Mbps), used (booked Mbps), up
        self.ip_to_host = {}
        self.load_topology(topology_file)
        self.reservations = {}                    # id -> Reservation
        self.next_res_id = 1
        self.lock = threading.Lock()              # guards the table and link usage
        self.counts = dict.fromkeys(["ACCEPT", NO_CAPACITY, CONFLICT, INVALID, UNAUTHORIZED, CONTROLLER_ERROR], 0)
        self.setup_times = []
        self.stop_event = threading.Event()
        self.expiry_thread = None
        self.link_watcher = None

    def load_topology(self, topology_file):
        with open(topology_file) as f:
            topo = json.load(f)
        nodes = set(topo["hosts"]) | set(topo["switches"])
        self.ip_to_host = {info["ip"]: name for name, info in topo["hosts"].items()}
        self.graph.add_nodes_from(nodes)
        for link in topo["links"]:
            if link["a"] in nodes and link["b"] in nodes:          # skip the server's own link
                for a, b in ((link["a"], link["b"]), (link["b"], link["a"])):
                    self.graph.add_edge(a, b, cap=float(link["bw"]), used=0.0, up=True)

    # ---------------- paths and link usage ----------------

    @staticmethod
    def path_links(path):
        return list(zip(path[:-1], path[1:]))     # ['h1','s1','s2'] -> [('h1','s1'), ('s1','s2')]

    def path_has_room(self, path, mbps):
        return all(self.graph[a][b]["cap"] - self.graph[a][b]["used"] >= mbps for a, b in self.path_links(path))

    def find_path(self, src_host, dst_host, mbps):
        """Shortest path first (Path A), then the next ones (Path B). Baseline takes the shortest, no checks."""
        working = nx.subgraph_view(self.graph, filter_edge=lambda a, b: self.graph[a][b]["up"])
        try:
            for tried, path in enumerate(nx.shortest_simple_paths(working, src_host, dst_host)):
                if self.baseline_mode or self.path_has_room(path, mbps):
                    return path
                if tried + 1 >= self.max_paths_to_try:
                    break
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass
        return None

    def change_usage(self, path, mbps):
        for a, b in self.path_links(path):
            self.graph[a][b]["used"] = max(0.0, self.graph[a][b]["used"] + mbps)   # max: no -0.0000001

    # ---------------- requests ----------------

    def reject(self, reason, detail):
        self.counts[reason] += 1                 # always called with the lock held
        log.info("REJECT %s %s", reason, detail)
        return ("REJECT", reason, detail)

    def request(self, user, src_ip, dst_ip, mbps, seconds):
        """('ACCEPT', id, path, end_time) or ('REJECT', reason, detail)"""
        started = time.time()
        try:
            mbps, seconds = float(mbps), float(seconds)
            bad = None if mbps > 0 and seconds > 0 else "mbps and seconds must be > 0"
        except (TypeError, ValueError):
            bad = "mbps/seconds not a number"
        if src_ip not in self.ip_to_host or dst_ip not in self.ip_to_host or src_ip == dst_ip:
            bad = "unknown or same host"
        with self.lock:
            if bad:
                return self.reject(INVALID, bad)
            # same src -> dst already booked? (the switch rules match on src + dst IP, so two would clash)
            if not self.baseline_mode and any(r.status in ("PENDING", "ACTIVE") and (r.src_ip, r.dst_ip) == (src_ip, dst_ip)
                                              for r in self.reservations.values()):
                return self.reject(CONFLICT, "%s -> %s already reserved" % (src_ip, dst_ip))
            path = self.find_path(self.ip_to_host[src_ip], self.ip_to_host[dst_ip], mbps)
            if path is None:
                return self.reject(NO_CAPACITY, "no path with %.1f Mbps free" % mbps)
            # book now, inside the lock, so no other client can take the same bandwidth
            res = Reservation(self.next_res_id, user, src_ip, dst_ip, mbps, time.time() + seconds, path)
            self.next_res_id += 1
            self.change_usage(path, mbps)
            self.reservations[res.res_id] = res

        # install outside the lock (a REST call can be slow), undo the booking if it fails
        installed = self.controller.install(res.res_id, src_ip, dst_ip, mbps, path)
        with self.lock:
            if not installed:
                if res.status == "PENDING":
                    self.change_usage(res.path, -mbps)
                res.status = "FAILED"
                return self.reject(CONTROLLER_ERROR, "controller did not install the rules")
            if res.status == "PENDING":
                res.status = "ACTIVE"
            self.counts["ACCEPT"] += 1
            self.setup_times.append(time.time() - started)
        log.info("ACCEPT id=%d %s->%s %.1fMbps %ds path=%s", res.res_id, src_ip, dst_ip, mbps, seconds, res.path)
        return ("ACCEPT", res.res_id, res.path, res.end_time)

    def finish(self, res, status):
        """Free the bandwidth (lock held). The caller deletes the rules after releasing the lock."""
        self.change_usage(res.path, -res.mbps)
        res.status = status

    def cancel(self, res_id, user=None):
        with self.lock:
            res = self.reservations.get(res_id)
            if res is None:
                return ("ERROR", "NOT_FOUND")
            if user is not None and res.user != user:
                return ("ERROR", UNAUTHORIZED)
            if res.status != "ACTIVE":
                return ("ERROR", "NOT_ACTIVE")
            self.finish(res, "CANCELLED")
        self.controller.remove(res_id)
        log.info("CANCEL id=%d", res_id)
        return ("OK",)

    def get_status(self, res_id):
        with self.lock:
            return self.reservations.get(res_id)

    def list_for_user(self, user):
        with self.lock:
            return [r for r in self.reservations.values() if r.user == user and r.status == "ACTIVE"]

    # ---------------- expiry ----------------

    def expire_old_reservations(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            expired = [r for r in self.reservations.values() if r.status == "ACTIVE" and r.end_time <= now]
            for res in expired:
                self.finish(res, "EXPIRED")
        for res in expired:
            self.controller.remove(res.res_id)
            log.info("EXPIRED id=%d", res.res_id)
        return [r.res_id for r in expired]

    def expiry_loop(self):
        while not self.stop_event.wait(self.expiry_interval):
            try:
                self.expire_old_reservations()
            except Exception as e:                # never let the timer thread die
                log.error("expiry loop error: %s", e)

    def start(self):
        self.stop_event.clear()
        self.expiry_thread = threading.Thread(target=self.expiry_loop, daemon=True)
        self.expiry_thread.start()

    def stop(self):
        self.stop_event.set()
        if self.expiry_thread:
            self.expiry_thread.join(timeout=2)

    # ---------------- link failure (reported by the link watcher) ----------------

    def set_link(self, a, b, up):
        self.graph[a][b]["up"] = self.graph[b][a]["up"] = up

    def link_down(self, a, b):
        """Move every booking that uses a-b to another path with room. Returns [(id, new_path or None)]."""
        moved, failed = [], []
        with self.lock:
            if not self.graph.has_edge(a, b):
                return []
            self.set_link(a, b, False)
            for res in self.reservations.values():
                links = self.path_links(res.path)
                if res.status != "ACTIVE" or ((a, b) not in links and (b, a) not in links):
                    continue
                self.change_usage(res.path, -res.mbps)       # free the old path so it can't block itself
                new_path = self.find_path(self.ip_to_host[res.src_ip], self.ip_to_host[res.dst_ip], res.mbps)
                if new_path:
                    self.change_usage(new_path, res.mbps)
                    res.path = new_path
                    moved.append(res)
                else:
                    res.status = "FAILED"
                    failed.append(res)
        for res in moved:
            self.controller.reroute(res.res_id, res.path)
        for res in failed:
            self.controller.remove(res.res_id)
        log.warning("link %s-%s down: moved %s, failed %s", a, b,
                    [r.res_id for r in moved], [r.res_id for r in failed])
        return [(r.res_id, r.path) for r in moved] + [(r.res_id, None) for r in failed]

    def link_up(self, a, b):
        with self.lock:
            if self.graph.has_edge(a, b):
                self.set_link(a, b, True)

    # ---------------- numbers for utilization and evaluation ----------------

    def link_usage(self):
        """{'s1->s2': (booked, capacity, up), ...}"""
        with self.lock:
            return {"%s->%s" % (a, b): (d["used"], d["cap"], d["up"]) for a, b, d in self.graph.edges(data=True)}

    def active_count(self):
        with self.lock:
            return sum(r.status == "ACTIVE" for r in self.reservations.values())

    def summary(self):
        with self.lock:
            total, accepted = sum(self.counts.values()), self.counts["ACCEPT"]
            return {"total": total, "accepted": accepted, "rejected": total - accepted,
                    "acceptance_ratio": accepted / total if total else 0,
                    "avg_setup_ms": 1000 * sum(self.setup_times) / len(self.setup_times) if self.setup_times else 0,
                    "reasons": dict(self.counts)}
