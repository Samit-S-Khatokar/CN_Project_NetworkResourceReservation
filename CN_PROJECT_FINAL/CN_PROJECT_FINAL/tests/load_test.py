# load_test.py - Member A: N clients connect at the same moment and each books a random host pair.
# The server must answer every one (accept or reject) without crashing.
#   python3 tests/load_test.py [clients] [host]      (server must be running)

import concurrent.futures
import csv
import os
import random
import socket
import sys
import threading
import time

HOST = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
PORT = 9000
CLIENTS = int(sys.argv[1]) if len(sys.argv) > 1 else 50
USERS = {"user1": "demo-token-user1", "user2": "demo-token-user2"}
HOST_IPS = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]

results = {"ACCEPT": 0, "REJECT": 0, "ERROR": 0}
reply_times = []
lock = threading.Lock()


def make_request(index):
    user = "user1" if index % 2 == 0 else "user2"
    src, dst = random.sample(HOST_IPS, 2)
    message = f"REQUEST {user} {src} {dst} 1 30"

    try:
        start = time.perf_counter()
        with socket.create_connection((HOST, PORT), timeout=10) as sock:
            reader = sock.makefile("r", encoding="utf-8")
            sock.sendall(f"AUTH {user} {USERS[user]}\n".encode())
            if reader.readline().strip() != "AUTH_OK":
                raise ConnectionError("auth failed")
            sock.sendall((message + "\n").encode())
            response = reader.readline().strip()
        elapsed = time.perf_counter() - start

        kind = response.split()[0] if response else "ERROR"
        with lock:
            results[kind if kind in results else "ERROR"] += 1
            reply_times.append(elapsed)
        print(f"Client {index + 1}: {response}")

    except (OSError, ConnectionError) as exc:
        with lock:
            results["ERROR"] += 1
        print(f"Client {index + 1}: ERROR {exc}")


def main():
    start = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=CLIENTS) as executor:
        list(executor.map(make_request, range(CLIENTS)))

    elapsed = time.perf_counter() - start
    avg_ms = 1000 * sum(reply_times) / len(reply_times) if reply_times else 0

    print("\n=== Load Test Results ===")
    print(f"Clients: {CLIENTS}")
    print(f"Accepted: {results['ACCEPT']}")
    print(f"Rejected: {results['REJECT']}")
    print(f"Errors: {results['ERROR']}")
    print(f"Average reply time: {avg_ms:.1f} ms")
    print(f"Elapsed time: {elapsed:.3f} seconds")

    if results["ERROR"] == 0 and results["ACCEPT"] + results["REJECT"] == CLIENTS:
        print("PASS: every client got an answer, server didn't crash.")
    else:
        print("FAIL: some clients got no answer, check logs/server.log")

    os.makedirs("results", exist_ok=True)
    out = os.path.join("results", "overload_test.csv")
    new_file = not os.path.exists(out)
    with open(out, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["clients", "accepted", "rejected", "errors", "avg_reply_ms", "elapsed_s"])
        writer.writerow([CLIENTS, results["ACCEPT"], results["REJECT"], results["ERROR"], round(avg_ms, 1), round(elapsed, 3)])


if __name__ == "__main__":
    main()
