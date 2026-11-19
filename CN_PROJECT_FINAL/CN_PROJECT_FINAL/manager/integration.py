# integration.py - Member C: builds the manager, controller client, utilization tracker and
# link watcher in one call, so Member A's server only needs start_backend() / stop_backend().
# Environment: NRRS_MOCK=1 (fake controller), NRRS_BASELINE=1 (no admission control),
#              NRRS_CONTROLLER (REST address), NRRS_UTIL_CSV (utilization file), NRRS_TOPOLOGY
import logging
import os

from manager.reservation_manager import ReservationManager
from manager.controller_client import RyuClient, MockController
from manager.utilization_tracker import UtilizationTracker
from manager.link_watcher import LinkWatcher

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start_backend():
    env = os.environ.get
    mock, baseline = env("NRRS_MOCK") == "1", env("NRRS_BASELINE") == "1"
    controller = MockController() if mock else RyuClient(env("NRRS_CONTROLLER", "http://127.0.0.1:8080"))
    manager = ReservationManager(env("NRRS_TOPOLOGY", os.path.join(PROJECT_DIR, "config", "topology.json")),
                                 controller, baseline_mode=baseline)
    manager.start()
    tracker = UtilizationTracker(manager, env("NRRS_UTIL_CSV", os.path.join(PROJECT_DIR, "results", "utilization.csv")),
                                 controller=None if mock else controller,
                                 run_label="baseline" if baseline else "reservation")
    tracker.start()
    if not mock:                         # link changes come from the real controller only
        manager.link_watcher = LinkWatcher(manager, controller)
        manager.link_watcher.start()
    logging.getLogger("integration").info("backend up (mock=%s, baseline=%s)", mock, baseline)
    return manager, tracker


def stop_backend(manager, tracker):
    if manager.link_watcher:
        manager.link_watcher.stop()
    tracker.stop()
    manager.stop()
