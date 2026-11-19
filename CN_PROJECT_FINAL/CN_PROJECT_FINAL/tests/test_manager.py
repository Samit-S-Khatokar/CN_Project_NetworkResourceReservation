# test_manager.py - Member C: reservation manager on the real topology, with the mock controller
# Path A s1-s2-s4 = 10 Mbps, Path B s1-s3-s4 = 20 Mbps (each direction); h1, h3 on s1; h2, h4 on s4

import os
import sys
import time
import threading
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from manager.reservation_manager import ReservationManager
from manager.controller_client import MockController
from manager.link_watcher import LinkWatcher

TOPO = os.path.join(PROJECT_DIR, "config", "topology.json")
H1, H2, H3, H4 = "10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"
PATH_A = ["h1", "s1", "s2", "s4", "h2"]


class TestReservationManager(unittest.TestCase):

    def setUp(self):
        self.ctrl = MockController()
        self.mgr = ReservationManager(TOPO, self.ctrl)

    def test_accept_takes_path_a_first(self):
        result = self.mgr.request("user1", H1, H2, 5, 30)
        self.assertEqual(result[0], "ACCEPT")
        self.assertEqual(result[2], PATH_A)
        self.assertEqual(self.ctrl.rule_count(), 1)

    def test_invalid_requests(self):
        self.assertEqual(self.mgr.request("a", H1, "10.9.9.9", 5, 10)[1], "INVALID")   # unknown host
        self.assertEqual(self.mgr.request("a", H1, H1, 5, 10)[1], "INVALID")           # same host
        self.assertEqual(self.mgr.request("a", H1, H2, -5, 10)[1], "INVALID")          # negative bw
        self.assertEqual(self.mgr.request("a", H1, H2, 0, 10)[1], "INVALID")           # zero bw
        self.assertEqual(self.mgr.request("a", H1, H2, "abc", 10)[1], "INVALID")       # not a number
        self.assertEqual(self.mgr.request("a", H1, H2, 5, 0)[1], "INVALID")            # zero time

    def test_uses_path_b_when_path_a_is_full(self):
        first = self.mgr.request("user1", H1, H2, 8, 30)
        second = self.mgr.request("user2", H3, H4, 8, 30)    # path A only has 2 left
        self.assertEqual(first[2][2], "s2")
        self.assertEqual(second[2][2], "s3")

    def test_reject_no_capacity(self):
        self.mgr.request("user1", H1, H2, 10, 30)     # fills path A
        self.mgr.request("user2", H3, H4, 20, 30)     # fills path B
        third = self.mgr.request("user1", H1, H4, 1, 30)
        self.assertEqual(third[:2], ("REJECT", "NO_CAPACITY"))

    def test_links_are_full_duplex(self):
        # 10 Mbps each way on path A, so h1->h2 and h2->h1 can both have 10
        self.assertEqual(self.mgr.request("user1", H1, H2, 10, 30)[0], "ACCEPT")
        back = self.mgr.request("user2", H2, H1, 10, 30)
        self.assertEqual(back[0], "ACCEPT")
        self.assertEqual(back[2], list(reversed(PATH_A)))

    def test_same_switch_hosts_dont_use_core_links(self):
        r = self.mgr.request("user1", H1, H3, 50, 30)    # both on s1
        self.assertEqual(r[2], ["h1", "s1", "h3"])

    def test_duplicate_flow_conflict(self):
        self.mgr.request("user1", H1, H2, 2, 30)
        dup = self.mgr.request("user2", H1, H2, 2, 30)
        self.assertEqual(dup[1], "CONFLICT")

    def test_cancel_frees_bandwidth(self):
        r = self.mgr.request("user1", H1, H2, 10, 30)
        self.mgr.request("user2", H3, H4, 20, 30)
        self.assertEqual(self.mgr.request("user2", H3, H2, 5, 30)[1], "NO_CAPACITY")
        self.assertEqual(self.mgr.cancel(r[1]), ("OK",))
        self.assertEqual(self.mgr.request("user2", H3, H2, 5, 30)[0], "ACCEPT")
        self.assertEqual(self.mgr.cancel(r[1])[1], "NOT_ACTIVE")    # cancel twice
        self.assertEqual(self.mgr.cancel(999)[1], "NOT_FOUND")

    def test_cancel_by_wrong_user(self):
        r = self.mgr.request("user1", H1, H2, 2, 30)
        self.assertEqual(self.mgr.cancel(r[1], user="user2")[1], "UNAUTHORIZED")

    def test_expiry_frees_bandwidth_and_rules(self):
        r = self.mgr.request("user1", H1, H2, 10, 1)
        self.assertEqual(self.ctrl.rule_count(), 1)
        expired = self.mgr.expire_old_reservations(now=time.time() + 2)
        self.assertEqual(expired, [r[1]])
        self.assertEqual(self.mgr.get_status(r[1]).status, "EXPIRED")
        self.assertEqual(self.ctrl.rule_count(), 0)
        for used, cap, up in self.mgr.link_usage().values():
            self.assertEqual(used, 0)

    def test_expiry_thread_runs_by_itself(self):
        self.mgr.expiry_interval = 0.1
        self.mgr.start()
        r = self.mgr.request("user1", H1, H2, 5, 0.3)
        time.sleep(0.8)
        self.mgr.stop()
        self.assertEqual(self.mgr.get_status(r[1]).status, "EXPIRED")

    def test_controller_failure_rolls_back(self):
        mgr = ReservationManager(TOPO, MockController(always_fail=True))
        r = mgr.request("user1", H1, H2, 5, 30)
        self.assertEqual(r[1], "CONTROLLER_ERROR")
        for used, cap, up in mgr.link_usage().values():
            self.assertEqual(used, 0)

    def test_link_down_reroutes_to_path_b(self):
        r = self.mgr.request("user1", H1, H2, 5, 30)
        moved = self.mgr.link_down("s1", "s2")
        self.assertEqual(moved, [(r[1], ["h1", "s1", "s3", "s4", "h2"])])
        self.assertEqual(self.ctrl.installed_rules[r[1]]["path"], ["h1", "s1", "s3", "s4", "h2"])
        # the broken link isn't booked any more, the new one is
        usage = self.mgr.link_usage()
        self.assertEqual(usage["s1->s2"][0], 0)
        self.assertEqual(usage["s1->s3"][0], 5)

    def test_link_down_no_room_fails_reservation(self):
        a = self.mgr.request("user1", H1, H2, 8, 30)       # path A
        b = self.mgr.request("user2", H3, H4, 18, 30)      # path B, only 2 left there
        moved = self.mgr.link_down("s1", "s2")
        self.assertEqual(moved, [(a[1], None)])
        self.assertEqual(self.mgr.get_status(a[1]).status, "FAILED")
        self.assertEqual(self.mgr.get_status(b[1]).status, "ACTIVE")

    def test_concurrent_requests_never_overbook(self):
        # 100 threads at the same time, no link may ever go over its capacity
        ips = [H1, H2, H3, H4]

        def worker(i):
            src = ips[i % 4]
            dst = ips[(i + 1 + i // 4) % 4]
            if src != dst:
                self.mgr.request("u%d" % i, src, dst, 3, 30)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for link, (used, cap, up) in self.mgr.link_usage().items():
            self.assertLessEqual(used, cap, link)

    def test_baseline_overbooks(self):
        base = ReservationManager(TOPO, MockController(), baseline_mode=True)
        for i in range(5):
            self.assertEqual(base.request("u", H1, H2, 5, 30)[0], "ACCEPT")
        self.assertEqual(base.link_usage()["s1->s2"][0], 25)    # 25 Mbps booked on a 10 Mbps link

    def test_link_watcher_reacts_to_link_down(self):
        r = self.mgr.request("user1", H1, H2, 5, 30)
        fake_links = [{"a": "s1", "b": "s2", "up": True}]
        self.ctrl.get_links = lambda: fake_links      # pretend B's controller answers this
        watcher = LinkWatcher(self.mgr, self.ctrl)
        watcher.check_once()
        fake_links[0]["up"] = False
        watcher.check_once()
        self.assertEqual(self.mgr.get_status(r[1]).path, ["h1", "s1", "s3", "s4", "h2"])


if __name__ == "__main__":
    unittest.main()
