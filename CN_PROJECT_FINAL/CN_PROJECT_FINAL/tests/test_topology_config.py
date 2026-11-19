# test_topology_config.py - port numbers and forwarding worked out from config/topology.json

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.topology_config import load_topology, switch_ports, forwarding_tables, spanning_tree


class TestTopologyConfig(unittest.TestCase):

    def setUp(self):
        self.topo = load_topology()

    def test_ports_match_member_b(self):
        ports = switch_ports(self.topo)
        self.assertEqual(ports["s1"], {"h1": 1, "h3": 2, "s2": 3, "s3": 4, "srv": 5})
        self.assertEqual(ports["s4"], {"h2": 1, "h4": 2, "s2": 3, "s3": 4})

    def test_flood_tree_uses_path_a(self):
        # B's tree_ports were {1: [1,2,3], 2: [1,2], 3: [], 4: [1,2,3]} (+ port 5 for the server now)
        flood, _ = forwarding_tables(self.topo)
        self.assertEqual(flood[2], [1, 2])
        self.assertEqual(flood[4], [1, 2, 3])
        self.assertIn(3, flood[1])
        self.assertIn(5, flood[1])

    def test_tree_has_no_loop(self):
        self.assertEqual(len(spanning_tree(self.topo)), len(self.topo["switches"]) - 1)

    def test_tree_moves_to_path_b_when_s1_s2_fails(self):
        tree = spanning_tree(self.topo, [("s1", "s2")])
        self.assertNotIn(("s1", "s2"), tree)
        self.assertIn(("s1", "s3"), tree)
        self.assertIn(("s3", "s4"), tree)
        _, macs = forwarding_tables(self.topo, [("s1", "s2")])
        self.assertEqual(macs[1]["00:00:00:00:00:02"], 4)     # s1 now sends to h2 via s3 (port 4)


if __name__ == "__main__":
    unittest.main()
