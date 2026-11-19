# controller_client.py - Member C: talks to Member B's controller REST API (port 8080).
# MockController pretends to be the controller, so the manager can be tested without Mininet.
import logging
import threading

import requests

log = logging.getLogger("controller")


class RyuClient:
    def __init__(self, base_url="http://127.0.0.1:8080", timeout=2.0):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def send(self, method, path, body=None):
        """True if the controller answered 2xx."""
        try:
            resp = requests.request(method, self.base_url + path, json=body, timeout=self.timeout)
        except requests.RequestException as e:
            log.error("%s %s failed: %s", method, path, e)
            return False
        if resp.status_code >= 300:
            log.warning("%s %s -> %d %s", method, path, resp.status_code, resp.text[:200])
        return resp.status_code < 300

    def get(self, path):
        try:
            return requests.get(self.base_url + path, timeout=self.timeout).json()
        except (requests.RequestException, ValueError) as e:
            log.error("GET %s failed: %s", path, e)
            return None

    def install(self, res_id, src_ip, dst_ip, mbps, path):
        return self.send("POST", "/reserve", {"id": res_id, "src": src_ip, "dst": dst_ip, "mbps": mbps, "path": path})

    def remove(self, res_id):
        return self.send("DELETE", "/reserve/%d" % res_id)

    def reroute(self, res_id, path):
        return self.send("PUT", "/reserve/%d" % res_id, {"path": path})

    def stats(self):
        return self.get("/stats") or {}

    def get_links(self):
        data = self.get("/links")
        return data["links"] if data else None


class MockController:
    """Fake controller: only remembers which rules would be installed."""

    def __init__(self, always_fail=False):
        self.always_fail = always_fail          # to test the rollback
        self.installed_rules = {}
        self.lock = threading.Lock()

    def install(self, res_id, src_ip, dst_ip, mbps, path):
        if self.always_fail:
            return False
        with self.lock:
            self.installed_rules[res_id] = {"src": src_ip, "dst": dst_ip, "mbps": mbps, "path": path}
        return True

    def remove(self, res_id):
        with self.lock:
            self.installed_rules.pop(res_id, None)
        return True

    def reroute(self, res_id, path):
        with self.lock:
            self.installed_rules[res_id]["path"] = path
        return True

    def stats(self):
        return {}

    def get_links(self):
        return None

    def rule_count(self):
        with self.lock:
            return len(self.installed_rules)
