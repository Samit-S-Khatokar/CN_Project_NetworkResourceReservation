# client.py - Member A: client for the reservation server
#   python3 client/client.py                         interactive menu
#   python3 client/client.py --show --host 10.0.0.254 "REQUEST user1 10.0.0.1 10.0.0.2 5 60"
# Inside Mininet the server is 10.0.0.254; on the laptop itself it is 127.0.0.1.
import argparse
import os
import socket

HOST = os.environ.get("NRRS_SERVER", "127.0.0.1")
PORT = int(os.environ.get("NRRS_SERVER_PORT", "9000"))


def send_request(user, token, message, host=None, port=None, show=False):
    """AUTH, send one line, return the reply. show=True prints the whole conversation."""
    with socket.create_connection((host or HOST, port or PORT), timeout=5) as sock:
        reader = sock.makefile("r", encoding="utf-8")
        sock.sendall(f"AUTH {user} {token}\n".encode())
        auth = reader.readline().strip()
        if show:
            print(f"client -> AUTH {user} {'*' * len(token)}\nserver <- {auth}")
        if auth != "AUTH_OK":
            return auth
        sock.sendall((message + "\n").encode())
        reply = reader.readline().strip()
        if not reply:
            raise ConnectionError("server closed the connection")
        if show:
            print(f"client -> {message}\nserver <- {reply}")
        return reply


def interactive():
    menu = "\n1. Request bandwidth\n2. Cancel reservation\n3. Check reservation\n4. List my reservations\n5. Exit"
    while True:
        print(menu)
        choice = input("Choose: ").strip()
        if choice == "5":
            break
        if choice not in ("1", "2", "3", "4"):
            continue
        user, token = input("User (user1/user2): ").strip(), input("Token: ").strip()
        if choice == "1":
            fields = [input(q + ": ").strip() for q in ("Source IP", "Destination IP", "Mbps", "Seconds")]
            message = f"REQUEST {user} " + " ".join(fields)
        elif choice == "4":
            message = f"LIST {user}"
        else:
            message = ("CANCEL " if choice == "2" else "STATUS ") + input("Reservation ID: ").strip()
        try:
            print("Server:", send_request(user, token, message))
        except OSError as error:
            print("Request failed:", error)


def main():
    global HOST, PORT
    parser = argparse.ArgumentParser(description="Reservation client")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--user", default="user1")
    parser.add_argument("--token", default="demo-token-user1")
    parser.add_argument("--show", action="store_true", help="print the whole conversation")
    parser.add_argument("command", nargs="?", help='e.g. "REQUEST user1 10.0.0.1 10.0.0.2 5 60"')
    args = parser.parse_args()
    HOST, PORT = args.host, args.port
    if not args.command:
        return interactive()
    try:
        reply = send_request(args.user, args.token, args.command, show=args.show)
        if not args.show:
            print(reply)
    except OSError as error:
        print("ERROR", error)


if __name__ == "__main__":
    main()
