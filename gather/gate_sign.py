"""Signing requests between the agent and the policy gate (HMAC-SHA256).

Only someone with GATE_SECRET can use the gate, and an old request can't be
replayed after 5 minutes (or twice within them).
"""
import hashlib
import hmac
import time

MAX_SKEW = 300


def sign(secret: str, body: bytes, now: int | None = None) -> dict:
    ts = str(int(now or time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Gate-Time": ts, "X-Gate-Sig": sig}


def verify(secret: str, body: bytes, ts: str, sig: str) -> bool:
    try:
        if abs(time.time() - int(ts)) > MAX_SKEW:
            return False
    except (TypeError, ValueError):
        return False
    good = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig or "")
