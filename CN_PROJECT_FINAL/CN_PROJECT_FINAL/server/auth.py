# auth.py - Member A: checks the "AUTH <user> <token>" line every client sends first
import hmac

USERS = {"user1": "demo-token-user1", "user2": "demo-token-user2"}   # demo credentials


def authenticate(user, token):
    expected = USERS.get(user)
    return expected is not None and hmac.compare_digest(expected, token)
