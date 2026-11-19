# reservation_server.py - Member A (integrated by Member C)
# Threaded TCP server on port 9000. A client sends "AUTH <user> <token>" first,
# then any number of REQUEST / STATUS / LIST / CANCEL lines. Decisions are made
# by Member C's reservation manager, which drives Member B's SDN controller.
#   python3 -m server.reservation_server             (controller must be running)
#   NRRS_MOCK=1 python3 -m server.reservation_server (no controller, for tests)
import logging
import os
import signal
import socket
import sys
import threading

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from server.protocol import parse_message, format_accept, format_reject
from server.auth import authenticate
from manager.integration import start_backend, stop_backend

HOST = "0.0.0.0"   # all interfaces, so Mininet hosts reach us on 10.0.0.254
PORT = int(os.environ.get("NRRS_SERVER_PORT", "9000"))
CLIENT_TIMEOUT = 120

os.makedirs(os.path.join(PROJECT_DIR, "logs"), exist_ok=True)
logging.basicConfig(filename=os.path.join(PROJECT_DIR, "logs", "server.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
manager = None


def process_request(message, user):
    try:
        req = parse_message(message)
    except ValueError:
        return format_reject("INVALID")
    action = req["action"]
    if action in ("REQUEST", "LIST") and req["user"] != user:   # only for yourself
        return format_reject("UNAUTHORIZED")

    if action == "REQUEST":
        result = manager.request(user, req["src_ip"], req["dst_ip"], req["mbps"], req["seconds"])
        if result[0] != "ACCEPT":
            return format_reject(result[1])
        _, rid, path, expires_at = result
        return format_accept(rid, "-".join(path), round(expires_at, 3))

    if action == "LIST":
        ids = [str(r.res_id) for r in manager.list_for_user(user)]
        return "LIST " + (",".join(ids) or "NONE")

    rid = req["reservation_id"]          # CANCEL / STATUS: ids are numbers (also the OpenFlow cookie)
    if not rid.isdigit():
        return "ERROR NOT_FOUND"
    if action == "CANCEL":
        result = manager.cancel(int(rid), user=user)
        return "OK" if result[0] == "OK" else "ERROR " + result[1]
    res = manager.get_status(int(rid))
    if res is None:
        return "ERROR NOT_FOUND"
    if res.user != user:
        return "ERROR UNAUTHORIZED"
    return f"STATUS {res.status} {res.res_id} {res.mbps} {round(res.end_time, 3)} {'-'.join(res.path)}"


def handle_client(conn, address):
    try:
        with conn:
            conn.settimeout(CLIENT_TIMEOUT)
            reader = conn.makefile("r", encoding="utf-8")
            parts = reader.readline().split()
            if len(parts) != 3 or parts[0].upper() != "AUTH" or not authenticate(parts[1], parts[2]):
                conn.sendall(b"REJECT UNAUTHORIZED\n")
                logging.warning("authentication failed from %s", address)
                return
            user = parts[1]
            conn.sendall(b"AUTH_OK\n")
            for line in reader:
                message = line.strip()
                try:
                    response = process_request(message, user) if message else format_reject("INVALID")
                except Exception:
                    logging.exception("request failed")
                    response = format_reject("INTERNAL_ERROR")
                logging.info("user=%s address=%s request=%r response=%r", user, address, message, response)
                conn.sendall((response + "\n").encode())
    except OSError:                       # includes timeouts and dropped connections
        pass


def stop_on_sigterm(signum, frame):
    raise KeyboardInterrupt               # so "kill" closes the CSV files like Ctrl+C


def main():
    global manager
    manager, tracker = start_backend()
    signal.signal(signal.SIGTERM, stop_on_sigterm)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(100)
        print(f"TCP reservation server running on {HOST}:{PORT}", flush=True)
        print("baseline mode" if manager.baseline_mode else "reservation mode (admission control on)", flush=True)
        try:
            while True:
                conn, address = server.accept()
                threading.Thread(target=handle_client, args=(conn, address), daemon=True).start()
        except KeyboardInterrupt:
            print("\nServer shutting down.")
        finally:
            stop_backend(manager, tracker)
            logging.info("summary: %s", manager.summary())


if __name__ == "__main__":
    main()
