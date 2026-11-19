# link_watcher.py - Member C: asks B's controller twice a second which links are up (GET /links).
# B's controller detects the failure; the manager picks the new path.
import logging
import threading

log = logging.getLogger("link_watcher")


class LinkWatcher:
    def __init__(self, manager, controller, interval=0.5):
        self.manager, self.controller, self.interval = manager, controller, interval
        self.last_state = {}                     # (a, b) -> up
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def check_once(self):
        for link in self.controller.get_links() or []:
            key, up = tuple(sorted((link["a"], link["b"]))), bool(link["up"])
            if up != self.last_state.get(key, True):
                log.warning("link %s-%s went %s", key[0], key[1], "UP" if up else "DOWN")
                (self.manager.link_up if up else self.manager.link_down)(*key)
            self.last_state[key] = up

    def run(self):
        while not self.stop_event.wait(self.interval):
            try:
                self.check_once()
            except Exception as e:               # never let the watcher thread die
                log.error("watcher error: %s", e)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)
