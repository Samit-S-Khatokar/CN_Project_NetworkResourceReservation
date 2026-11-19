# protocol.py - Member A: parses one protocol line and formats the replies
import ipaddress
import math
import re

NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
REASONS = {"NO_CAPACITY", "CONFLICT", "INVALID", "UNAUTHORIZED", "CONTROLLER_ERROR", "INTERNAL_ERROR"}


def parse_message(message):
    """REQUEST / CANCEL / STATUS / LIST line -> dict. Anything else raises ValueError('INVALID')."""
    parts = message.split()
    cmd = parts[0].upper() if parts else ""
    if cmd == "REQUEST" and len(parts) == 6:
        user, src, dst = parts[1:4]
        try:
            ipaddress.ip_address(src)
            ipaddress.ip_address(dst)
            mbps, seconds = float(parts[4]), float(parts[5])
        except ValueError:
            raise ValueError("INVALID")
        if NAME.fullmatch(user) and all(math.isfinite(v) and v > 0 for v in (mbps, seconds)):
            return {"action": cmd, "user": user, "src_ip": src, "dst_ip": dst, "mbps": mbps, "seconds": seconds}
    elif cmd in ("CANCEL", "STATUS") and len(parts) == 2:
        return {"action": cmd, "reservation_id": parts[1]}
    elif cmd == "LIST" and len(parts) == 2 and NAME.fullmatch(parts[1]):
        return {"action": cmd, "user": parts[1]}
    raise ValueError("INVALID")


def format_accept(reservation_id, path, expires_at):
    return f"ACCEPT {reservation_id} {path} {expires_at}"


def format_reject(reason):
    return f"REJECT {reason if reason in REASONS else 'INTERNAL_ERROR'}"
