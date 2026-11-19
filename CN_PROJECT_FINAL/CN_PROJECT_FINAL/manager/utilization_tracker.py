# utilization_tracker.py - Member C: every second, writes booked and measured Mbps
# for each link direction to a CSV file (one row per link per second).
import csv
import os
import threading
import time

COLUMNS = ["timestamp", "elapsed_s", "link", "reserved_mbps", "cap_mbps", "utilization",
           "measured_mbps", "link_up", "active_reservations", "run"]


class UtilizationTracker:
    def __init__(self, manager, csv_file, controller=None, run_label="", interval=1.0):
        self.manager, self.csv_file, self.controller = manager, csv_file, controller
        self.run_label, self.interval = run_label, interval      # run_label: "reservation" / "baseline"
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        os.makedirs(os.path.dirname(self.csv_file) or ".", exist_ok=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)

    def run(self):
        start = time.time()
        with open(self.csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            while True:
                now = time.time()
                measured = self.controller.stats().get("link_rates_mbps", {}) if self.controller else {}
                active = self.manager.active_count()
                for link, (used, cap, up) in self.manager.link_usage().items():
                    writer.writerow([round(now, 2), round(now - start, 2), link, round(used, 2), cap,
                                     round(used / cap, 4), measured.get(link, ""), int(up), active, self.run_label])
                f.flush()                                # readable even if we crash
                if self.stop_event.wait(self.interval):
                    break
