# topology_config.py - reads config/topology.json, the one description of the network
# shared by the Mininet script, the controller and the manager. Standard library only,
# because the Mininet script runs with sudo's python.
import json
import os
from collections import deque

TOPOLOGY_FILE = os.environ.get("NRRS_TOPOLOGY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "topology.json"))


def load_topology(path=None):
    with open(path or TOPOLOGY_FILE) as f:
        return json.load(f)


def dpid_of(switch):
    return int(switch[1:])            # Mininet gives s1 dpid 1, s2 dpid 2, ...


def switch_ports(topo):
    """{switch: {neighbour: port}}. Mininet numbers ports in the order links are added."""
    ports = {s: {} for s in topo["switches"]}
    for link in topo["links"]:
        for me, other in ((link["a"], link["b"]), (link["b"], link["a"])):
            if me in ports:
                ports[me][other] = len(ports[me]) + 1
    return ports


def port_to_neighbour(topo):
    """{dpid: {port: neighbour}}"""
    return {dpid_of(sw): {p: nb for nb, p in nbs.items()} for sw, nbs in switch_ports(topo).items()}


def endpoints(topo):
    """hosts + the server: {name: {'ip', 'mac', 'switch'}}"""
    return {**topo["hosts"], topo["server"]["name"]: topo["server"]}


def spanning_tree(topo, down_links=()):
    """Loop-free set of switch links (Kruskal), Path A first, skipping links that are down.
    The network has a loop (s1-s2-s4-s3), so broadcasts may only follow this tree."""
    down = {tuple(sorted(l)) for l in down_links}
    links = [tuple(sorted((l["a"], l["b"]))) for l in topo["links"]
             if l["a"] in topo["switches"] and l["b"] in topo["switches"]]
    preferred = [tuple(sorted(n.split("-"))) for n in topo["flood_tree"]]
    group = {s: s for s in topo["switches"]}

    def root(x):
        while group[x] != x:
            x = group[x]
        return x

    tree = set()
    for a, b in preferred + [l for l in links if l not in preferred]:
        if (a, b) not in down and root(a) != root(b):
            group[root(a)] = root(b)
            tree.add((a, b))
    return tree


def forwarding_tables(topo, down_links=()):
    """(flood, mac_ports): flood = {dpid: [ports]}, mac_ports = {dpid: {mac: port}} along the tree."""
    ports = switch_ports(topo)
    neighbours = {s: [] for s in topo["switches"]}
    for a, b in spanning_tree(topo, down_links):
        neighbours[a].append(b)
        neighbours[b].append(a)
    flood, mac_ports = {}, {}
    for sw in topo["switches"]:
        flood[dpid_of(sw)] = sorted(p for nb, p in ports[sw].items() if nb not in neighbours or nb in neighbours[sw])
        next_hop, queue = {sw: None}, deque([sw])       # BFS: first switch on the way to every other switch
        while queue:
            cur = queue.popleft()
            for nb in neighbours[cur]:
                if nb not in next_hop:
                    next_hop[nb] = nb if cur == sw else next_hop[cur]
                    queue.append(nb)
        table = mac_ports[dpid_of(sw)] = {}
        for name, info in endpoints(topo).items():
            if info["switch"] == sw:
                table[info["mac"]] = ports[sw][name]
            elif next_hop.get(info["switch"]):
                table[info["mac"]] = ports[sw][next_hop[info["switch"]]]
    return flood, mac_ports
