# sdn_controller.py - Member B (integrated by Member C)
# OS-Ken (Ryu fork) controller, OpenFlow 1.3 on port 6653, REST API on port 8080.
#  - normal traffic: learning switch, broadcasts only along a loop-free tree
#  - a reservation: priority-100 rule on every switch of its path + a meter (speed limit)
#    on the first switch, cookie = reservation id; priority-90 rule for replies
#  - best-effort meter on every core port = capacity - reserved, so floods can't take booked bandwidth
#  - port status -> link up/down (GET /links), port stats every second -> measured Mbps (GET /stats)
#  - NRRS_BASELINE=1: rules but no meters, for comparison
#   osken-manager controller/sdn_controller.py
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ether_types
from os_ken.lib import hub

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.topology_config import load_topology, switch_ports, port_to_neighbour, forwarding_tables, dpid_of

TOPO = load_topology()
PORTS = switch_ports(TOPO)                    # {'s1': {'h1': 1, 'h3': 2, 's2': 3, ...}}
PORT_NEIGHBOUR = port_to_neighbour(TOPO)      # {1: {1: 'h1', 2: 'h3', 3: 's2', ...}}
HOST_IPS = {name: info["ip"] for name, info in TOPO["hosts"].items()}
CORE_CAPACITY = {}                            # {('s1', 's2'): 10, ('s2', 's1'): 10, ...}
for l in TOPO["links"]:
    if l["a"] in TOPO["switches"] and l["b"] in TOPO["switches"]:
        CORE_CAPACITY[(l["a"], l["b"])] = CORE_CAPACITY[(l["b"], l["a"])] = l["bw"]

BASELINE = os.environ.get("NRRS_BASELINE", "0") == "1"
REST_PORT = int(os.environ.get("NRRS_REST_PORT", "8080"))
METER_BURST_SECONDS = 0.5     # a smaller burst cuts TCP to about half its booked rate
BE_METER_BASE = 50000         # best-effort meter id = 50000 + port
LEARNED_PRIORITY, RESERVE_BACK_PRIORITY, RESERVE_PRIORITY = 1, 90, 100
LEARNED_COOKIE = 0x7FFF0000   # tag on best-effort rules, so they can be wiped after a link change
ALL_COOKIE_BITS = 0xFFFFFFFFFFFFFFFF

# os-ken runs everything in green threads on one OS thread, so REST and OpenFlow handlers never overlap
reservations = {}             # id -> {"id", "src_ip", "dst_ip", "mbps", "path", "meter_switch"}
mac_to_port = {}              # {dpid: {mac: port}}
tree_ports = {}               # {dpid: [ports to flood on]}


class SDNController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self.link_up = {tuple(sorted(k)): True for k in CORE_CAPACITY}
        self.port_bytes = {}          # (dpid, port) -> (time, tx_bytes)
        self.port_rate_mbps = {}      # (dpid, port) -> Mbps
        self.rebuild_forwarding()
        ReservationAPI.app = self
        hub.spawn(self.run_rest_api)  # green thread, so REST calls can safely talk to the switches
        hub.spawn(self.port_stats_loop)
        self.logger.info("Reservation SDN controller started (baseline=%s, REST port %d)", BASELINE, REST_PORT)

    # ---------------- switches and normal forwarding ----------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, ev):
        dp = ev.msg.datapath
        parser, ofp = dp.ofproto_parser, dp.ofproto
        self.datapaths[dp.id] = dp
        to_controller = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=0, match=parser.OFPMatch(),
                                      instructions=[parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, to_controller)]))
        if not BASELINE:
            sw = "s%d" % dp.id
            for nb, port in PORTS[sw].items():
                if (sw, nb) in CORE_CAPACITY:
                    self.add_meter(dp, BE_METER_BASE + port, CORE_CAPACITY[(sw, nb)])
            self.update_best_effort_meters()
        self.logger.info("Switch connected: %s", dp.id)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp, in_port = msg.datapath, msg.match["in_port"]
        parser, ofp = dp.ofproto_parser, dp.ofproto
        eth = packet.Packet(msg.data).get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        table = mac_to_port.setdefault(dp.id, {})
        table.setdefault(eth.src, in_port)        # hosts known from the config are never re-learned
        out_port = table.get(eth.dst)
        if out_port is None:                      # unknown: flood along the tree only
            actions = [parser.OFPActionOutput(p) for p in tree_ports.get(dp.id, []) if p != in_port]
        else:
            actions = [parser.OFPActionOutput(out_port)]
            instructions = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            if not BASELINE and ("s%d" % dp.id, PORT_NEIGHBOUR[dp.id].get(out_port)) in CORE_CAPACITY:
                instructions.insert(0, parser.OFPInstructionMeter(BE_METER_BASE + out_port))
            dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=LEARNED_PRIORITY, cookie=LEARNED_COOKIE,
                                          match=parser.OFPMatch(in_port=in_port, eth_dst=eth.dst),
                                          instructions=instructions))
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id, in_port=in_port,
                                        actions=actions, data=data))

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status(self, ev):
        msg = ev.msg
        ofp = msg.datapath.ofproto
        neighbour = PORT_NEIGHBOUR.get(msg.datapath.id, {}).get(msg.desc.port_no)
        if neighbour not in TOPO["switches"]:
            return
        up = not (msg.desc.state & ofp.OFPPS_LINK_DOWN or msg.desc.config & ofp.OFPPC_PORT_DOWN)
        key = tuple(sorted(("s%d" % msg.datapath.id, neighbour)))
        if self.link_up.get(key) == up:
            return
        self.logger.warning("link %s-%s is now %s", key[0], key[1], "UP" if up else "DOWN")
        self.link_up[key] = up
        # new tree for best-effort traffic; reserved flows are moved by the manager (PUT /reserve/{id})
        self.rebuild_forwarding()
        for dp in self.datapaths.values():
            self.delete_flows(dp, LEARNED_COOKIE)

    def rebuild_forwarding(self):
        flood, known_macs = forwarding_tables(TOPO, [k for k, up in self.link_up.items() if not up])
        tree_ports.clear()
        tree_ports.update(flood)
        mac_to_port.clear()
        mac_to_port.update({dpid: dict(t) for dpid, t in known_macs.items()})

    # ---------------- monitoring ----------------

    def port_stats_loop(self):
        while True:
            for dp in list(self.datapaths.values()):
                dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
            hub.sleep(1)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        now = time.time()
        for stat in ev.msg.body:
            key = (ev.msg.datapath.id, stat.port_no)
            old = self.port_bytes.get(key)
            if old and now > old[0]:
                self.port_rate_mbps[key] = (stat.tx_bytes - old[1]) * 8 / (now - old[0]) / 1e6
            self.port_bytes[key] = (now, stat.tx_bytes)

    def link_rates(self):
        """measured Mbps leaving each switch towards each neighbour: {'s1->s2': 4.9, ...}"""
        return {"%s->%s" % (sw, nb): round(self.port_rate_mbps[(dpid_of(sw), p)], 3)
                for sw, nbs in PORTS.items() for nb, p in nbs.items() if (dpid_of(sw), p) in self.port_rate_mbps}

    # ---------------- reservation rules ----------------

    def add_meter(self, dp, meter_id, mbps, command=None):
        parser, ofp = dp.ofproto_parser, dp.ofproto
        rate = max(int(mbps * 1000), 100)        # kbps
        band = parser.OFPMeterBandDrop(rate=rate, burst_size=max(int(rate * METER_BURST_SECONDS), 100))
        dp.send_msg(parser.OFPMeterMod(datapath=dp, command=ofp.OFPMC_ADD if command is None else command,
                                       flags=ofp.OFPMF_KBPS | ofp.OFPMF_BURST, meter_id=meter_id, bands=[band]))

    def add_ip_flow(self, dp, src_ip, dst_ip, out_port, cookie, priority, meter_id=None):
        parser, ofp = dp.ofproto_parser, dp.ofproto
        instructions = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, [parser.OFPActionOutput(out_port)])]
        if meter_id is not None:
            instructions.insert(0, parser.OFPInstructionMeter(meter_id))
        dp.send_msg(parser.OFPFlowMod(datapath=dp, cookie=cookie, priority=priority, instructions=instructions,
                                      match=parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                                            ipv4_src=src_ip, ipv4_dst=dst_ip)))

    def delete_flows(self, dp, cookie):
        parser, ofp = dp.ofproto_parser, dp.ofproto
        dp.send_msg(parser.OFPFlowMod(datapath=dp, cookie=cookie, cookie_mask=ALL_COOKIE_BITS, table_id=ofp.OFPTT_ALL,
                                      command=ofp.OFPFC_DELETE, out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                                      match=parser.OFPMatch()))

    def install_reservation(self, res):
        """res['path'] like ['h1','s1','s2','s4','h2']: rules on every switch, meter on the first one."""
        path = res["path"]
        switches = path[1:-1]
        missing = [sw for sw in switches if dpid_of(sw) not in self.datapaths]
        if missing:
            raise RuntimeError("switch %s is not connected" % missing[0])
        for i, sw in enumerate(switches, start=1):
            dp = self.datapaths[dpid_of(sw)]
            meter_id = None
            if i == 1 and not BASELINE:          # limit the rate once, where the traffic enters
                meter_id, res["meter_switch"] = res["id"], dp.id
                self.add_meter(dp, meter_id, res["mbps"])
            self.add_ip_flow(dp, res["src_ip"], res["dst_ip"], PORTS[sw][path[i + 1]], res["id"], RESERVE_PRIORITY, meter_id)
            self.add_ip_flow(dp, res["dst_ip"], res["src_ip"], PORTS[sw][path[i - 1]], res["id"], RESERVE_BACK_PRIORITY)
        self.logger.info("installed reservation %s on %s (%.1f Mbps%s)", res["id"], "-".join(path),
                         res["mbps"], ", NO meter: baseline" if BASELINE else "")

    def remove_reservation(self, res):
        for dp in self.datapaths.values():
            self.delete_flows(dp, res["id"])
        dp = self.datapaths.get(res.pop("meter_switch", None))
        if dp is not None:
            dp.send_msg(dp.ofproto_parser.OFPMeterMod(datapath=dp, command=dp.ofproto.OFPMC_DELETE, meter_id=res["id"]))
        self.logger.info("removed rules of reservation %s", res["id"])

    def update_best_effort_meters(self):
        """Best-effort limit per core link direction = capacity - reserved. Example: s1->s2 is 10 Mbps
        with 5 booked, so other traffic on s1->s2 gets at most 5 and the booked flow keeps its rate."""
        if BASELINE:
            return
        reserved = {}
        for res in reservations.values():
            for hop in zip(res["path"][:-1], res["path"][1:]):
                if hop in CORE_CAPACITY:
                    reserved[hop] = reserved.get(hop, 0) + res["mbps"]
        for (a, b), cap in CORE_CAPACITY.items():
            dp = self.datapaths.get(dpid_of(a))
            if dp is not None:                   # keep a trickle for ARP and control traffic
                self.add_meter(dp, BE_METER_BASE + PORTS[a][b], max(cap - reserved.get((a, b), 0), 0.5),
                               command=dp.ofproto.OFPMC_MODIFY)

    def run_rest_api(self):
        RestServer(("0.0.0.0", REST_PORT), ReservationAPI).serve_forever()


class RestServer(HTTPServer):
    request_queue_size = 128     # default 5 refused clients when 40 users booked at once


class ReservationAPI(BaseHTTPRequestHandler):
    """POST /reserve, PUT /reserve/{id} (reroute), DELETE /reserve/{id}, GET /links, GET /stats"""
    app = None

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

    def reservation_id(self):
        if not self.path.startswith("/reserve/") or not self.path[9:].isdigit():
            return None
        return int(self.path[9:])

    def do_GET(self):
        if self.path == "/links":
            return self.send_json(200, {"links": [{"a": a, "b": b, "up": up} for (a, b), up in sorted(self.app.link_up.items())]})
        if self.path == "/stats":
            return self.send_json(200, {"active_reservations": list(reservations.values()),
                                        "link_rates_mbps": self.app.link_rates(), "baseline_mode": BASELINE})
        self.send_json(404, {"error": "use GET /stats or GET /links"})

    def do_POST(self):
        if self.path != "/reserve":
            return self.send_json(404, {"error": "use POST /reserve"})
        try:
            data = self.read_json()
            res = {"id": int(data["id"]), "src_ip": HOST_IPS.get(data["src"], data["src"]),
                   "dst_ip": HOST_IPS.get(data["dst"], data["dst"]), "mbps": float(data["mbps"]),
                   "path": list(data["path"])}
        except (KeyError, ValueError, TypeError) as exc:
            return self.send_json(400, {"error": "bad request: %s" % exc})
        if res["id"] in reservations:
            return self.send_json(409, {"error": "duplicate reservation id"})
        try:
            self.app.install_reservation(res)
        except (RuntimeError, KeyError) as exc:
            return self.send_json(503, {"error": str(exc)})
        reservations[res["id"]] = res
        self.app.update_best_effort_meters()
        self.send_json(201, {"status": "ACCEPTED", "reservation": res})

    def do_PUT(self):
        res = reservations.get(self.reservation_id())
        if res is None:
            return self.send_json(404, {"error": "reservation not found"})
        try:
            new_path = list(self.read_json()["path"])
        except (KeyError, ValueError, TypeError) as exc:
            return self.send_json(400, {"error": "bad request: %s" % exc})
        self.app.remove_reservation(res)
        res["path"] = new_path
        try:
            self.app.install_reservation(res)
        except (RuntimeError, KeyError) as exc:
            return self.send_json(503, {"error": str(exc)})
        self.app.update_best_effort_meters()
        self.send_json(200, {"status": "REROUTED", "reservation": res})

    def do_DELETE(self):
        res = reservations.pop(self.reservation_id(), None)
        if res is None:
            return self.send_json(404, {"error": "reservation not found"})
        self.app.remove_reservation(res)
        self.app.update_best_effort_meters()
        self.send_json(200, {"status": "CANCELLED", "reservation": res})

    def log_message(self, fmt, *args):
        pass
