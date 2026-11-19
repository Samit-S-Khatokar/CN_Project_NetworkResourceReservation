# sdn_topology.py - Member B (integrated by Member C): the Mininet network
#   4 hosts, 4 OpenFlow 1.3 switches, host links 100 Mbps
#   Path A: s1-s2-s4 (10 Mbps)   Path B: s1-s3-s4 (20 Mbps)
#   srv = the laptop itself, plugged into s1 as 10.0.0.254, so hosts can reach the TCP server
#   sudo python3 topology/sdn_topology.py      (controller must already be running)
import os
import sys
from functools import partial

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.topo import Topo
from mininet.link import TCLink
from mininet.node import OVSSwitch, RemoteController, Node

from config.topology_config import load_topology

TOPO = load_topology()
DATAPATH = os.environ.get("NRRS_DATAPATH", "user")   # WSL has no OVS kernel module, so userspace


class ReservationTopo(Topo):
    def build(self, topo_config=None):
        topo_config = topo_config or TOPO
        for name, info in topo_config["hosts"].items():
            self.addHost(name, ip=info["ip"] + "/24", mac=info["mac"])
        for name in topo_config["switches"]:
            self.addSwitch(name, protocols="OpenFlow13")          # 1.3 is needed for meters
        for link in topo_config["links"]:                         # same order as the JSON = same port numbers
            if topo_config["server"]["name"] not in (link["a"], link["b"]):
                self.addLink(link["a"], link["b"], cls=TCLink, bw=link["bw"])


def disable_offload(node, interfaces):
    # userspace OVS drops TCP packets whose checksum is left to the NIC
    for intf in interfaces:
        node.cmd("ethtool -K %s tx off rx off >/dev/null 2>&1" % intf)


def build_network(controller_ip="127.0.0.1", controller_port=6653, topo_config=None):
    from mininet.net import Mininet

    topo_config = topo_config or TOPO
    switch = partial(OVSSwitch, datapath="user") if DATAPATH == "user" else OVSSwitch
    net = Mininet(topo=ReservationTopo(topo_config=topo_config), switch=switch, controller=None,
                  link=TCLink, autoSetMacs=False)
    net.addController("c0", controller=RemoteController, ip=controller_ip, port=controller_port)

    server = topo_config["server"]
    bw = next(l["bw"] for l in topo_config["links"] if server["name"] in (l["a"], l["b"]))
    root = Node(server["name"], inNamespace=False)                # the laptop's own network namespace
    srv_link = net.addLink(root, net.get(server["switch"]), cls=TCLink, bw=bw)
    root.setIP(server["ip"] + "/24", intf=srv_link.intf1)
    root.setMAC(server["mac"], intf=srv_link.intf1)
    net.start()

    if DATAPATH == "user":
        for node in net.hosts + net.switches:
            disable_offload(node, [i for i in node.intfNames() if i != "lo"])
        disable_offload(root, [srv_link.intf1.name])
    net.staticArp()
    for h in net.hosts:                                           # staticArp doesn't cover srv
        h.cmd("arp -s %s %s" % (server["ip"], srv_link.intf1.MAC()))
        root.cmd("arp -s %s %s" % (h.IP(), h.MAC()))
    return net, root, srv_link


if __name__ == "__main__":
    from mininet.cli import CLI
    from mininet.log import setLogLevel

    setLogLevel("info")
    net, root, srv_link = build_network()
    srv_intf = srv_link.intf1.name               # read it now: net.stop() clears the link
    try:
        net.pingAll()
        print("*** TCP server reachable from the hosts at %s:9000" % TOPO["server"]["ip"])
        CLI(net)
    finally:
        net.stop()
        root.cmd("ip link del %s 2>/dev/null" % srv_intf)
