# test_server_robustness.py - Members A + C: the real TCP server (mock controller), spoken to over sockets

import os
import socket
import subprocess
import sys
import time
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"
PORT = 9107          # not 9000, so the test doesn't clash with a server you left running


def talk(lines, user="user1", token="demo-token-user1"):
    """Open one connection, AUTH, send each line, return the replies."""
    with socket.create_connection((HOST, PORT), timeout=3) as sock:
        reader = sock.makefile("r", encoding="utf-8")
        sock.sendall(f"AUTH {user} {token}\n".encode())
        replies = [reader.readline().strip()]
        for line in lines:
            sock.sendall((line + "\n").encode())
            replies.append(reader.readline().strip())
        return replies


class TestServerRobustness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, NRRS_MOCK="1", NRRS_SERVER_PORT=str(PORT),
                   NRRS_UTIL_CSV=os.path.join(PROJECT_DIR, "logs", "test_utilization.csv"))
        cls.server = subprocess.Popen([sys.executable, "-m", "server.reservation_server"], cwd=PROJECT_DIR,
                                      env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # wait until the server accepts connections
        for _ in range(50):
            try:
                socket.create_connection((HOST, PORT), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    # ---- A's original robustness tests ----

    def test_invalid_authentication_rejected(self):
        with socket.create_connection((HOST, PORT), timeout=3) as sock:
            reader = sock.makefile("r", encoding="utf-8")
            sock.sendall(b"AUTH user1 wrong-token\n")
            self.assertEqual(reader.readline().strip(), "REJECT UNAUTHORIZED")

    def test_missing_authentication_rejected(self):
        with socket.create_connection((HOST, PORT), timeout=3) as sock:
            reader = sock.makefile("r", encoding="utf-8")
            sock.sendall(b"REQUEST user1 10.0.0.1 10.0.0.2 2 60\n")
            self.assertEqual(reader.readline().strip(), "REJECT UNAUTHORIZED")

    def test_disconnect_does_not_crash_server(self):
        with socket.create_connection((HOST, PORT), timeout=3):
            pass
        self.assertEqual(talk([])[0], "AUTH_OK")

    # ---- added: the full protocol going through the real manager ----

    def test_request_status_list_cancel(self):
        replies = talk(["REQUEST user1 10.0.0.3 10.0.0.4 4 60"])
        parts = replies[1].split()
        self.assertEqual(parts[0], "ACCEPT")
        self.assertEqual(parts[2], "h3-s1-s2-s4-h4")     # real path now, not "pending"
        rid = parts[1]

        status = talk(["STATUS " + rid])[1].split()
        self.assertEqual(status[:3], ["STATUS", "ACTIVE", rid])
        self.assertIn(rid, talk(["LIST user1"])[1])
        self.assertEqual(talk(["CANCEL " + rid])[1], "OK")
        self.assertEqual(talk(["STATUS " + rid])[1].split()[1], "CANCELLED")

    def test_cannot_request_as_someone_else(self):
        self.assertEqual(talk(["REQUEST user2 10.0.0.1 10.0.0.2 1 60"])[1], "REJECT UNAUTHORIZED")

    def test_cannot_cancel_someone_elses_reservation(self):
        rid = talk(["REQUEST user1 10.0.0.4 10.0.0.3 1 60"])[1].split()[1]
        self.assertEqual(talk(["CANCEL " + rid], "user2", "demo-token-user2")[1], "ERROR UNAUTHORIZED")

    def test_bad_lines_get_invalid(self):
        replies = talk(["HELLO", "REQUEST user1 10.0.0.1", "REQUEST user1 10.0.0.1 10.0.0.9 5 60"])
        self.assertEqual(replies[1:], ["REJECT INVALID"] * 3)

    def test_too_much_bandwidth_gets_no_capacity(self):
        self.assertEqual(talk(["REQUEST user1 10.0.0.2 10.0.0.1 50 60"])[1], "REJECT NO_CAPACITY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
