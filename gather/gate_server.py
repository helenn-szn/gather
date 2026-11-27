"""The policy gate: runs on its own Sprite (gather-gate).

It is the only machine with the AgentMail and Telegram connectors. The agent
Sprite sends signed requests to POST /proxy; every one is checked by
gate_policy before it is forwarded, and every decision is written to an
audit log the agent can't touch.

Settings (.env on the gate Sprite): AGENTMAIL_GATEWAY, TELEGRAM_GATEWAY,
AGENTMAIL_INBOX, GATE_SECRET. Guest list: gate.json (scripts.gate_setup).
"""
import json
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from . import config, gate_sign, gateway
from .config import ROOT
from .gate_policy import Policy

GATE_FILE = ROOT / "gate.json"
AUDIT_FILE = ROOT / "gate_audit.jsonl"
_lock = threading.Lock()
_seen_sigs: "OrderedDict[str, float]" = OrderedDict()
_state = {"policy": None, "mtime": 0.0, "allowed": 0, "blocked": 0, "recent_blocks": []}


def _bases() -> dict:
    return {"agentmail": config.agentmail_base(), "telegram": config.telegram_base()}


def _fetch_message(message_id: str) -> dict:
    inbox = quote(config.inbox_id(), safe="")
    return gateway.call("GET", config.agentmail_base(),
                        f"/v0/inboxes/{inbox}/messages/{quote(message_id, safe='')}")


def policy() -> Policy:
    """Load gate.json, reloading it when the host updates the guest list."""
    mtime = GATE_FILE.stat().st_mtime if GATE_FILE.exists() else 0
    if _state["policy"] is None or mtime != _state["mtime"]:
        data = json.loads(GATE_FILE.read_text()) if GATE_FILE.exists() else {}
        data.setdefault("inbox", config.inbox_id())
        old = _state["policy"]
        _state["policy"] = Policy(data, fetch_message=_fetch_message)
        if old:  # keep learned groups and rate-limit history across reloads
            _state["policy"].groups |= old.groups
            _state["policy"].sent = old.sent
        _state["mtime"] = mtime
    return _state["policy"]


def _save_groups(p: Policy) -> None:
    data = json.loads(GATE_FILE.read_text()) if GATE_FILE.exists() else {}
    if set(data.get("groups", [])) != p.groups:
        data["groups"] = sorted(p.groups)
        GATE_FILE.write_text(json.dumps(data, indent=2))
        _state["mtime"] = GATE_FILE.stat().st_mtime


def audit(entry: dict) -> None:
    entry["at"] = time.time()
    with AUDIT_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[gate] {'ALLOW' if entry['allowed'] else 'BLOCK'} {entry['service']} "
          f"{entry['method']} {entry['path']}: {entry['reason']}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, obj) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed_body(self) -> bytes | None:
        length = min(int(self.headers.get("Content-Length") or 0), 64000)
        raw = self.rfile.read(length)
        ts, sig = self.headers.get("X-Gate-Time", ""), self.headers.get("X-Gate-Sig", "")
        if not gate_sign.verify(config.gate_secret(), raw, ts, sig):
            return None
        with _lock:  # no replays
            now = time.time()
            while _seen_sigs and now - next(iter(_seen_sigs.values())) > gate_sign.MAX_SKEW:
                _seen_sigs.popitem(last=False)
            if sig in _seen_sigs:
                return None
            _seen_sigs[sig] = now
        return raw

    def do_GET(self):
        self._send(200, {"gate": "Gather policy gate", "ok": True})

    def do_POST(self):
        raw = self._authed_body()
        if raw is None:
            return self._send(401, {"error": "bad or missing signature"})
        if self.path == "/stats":
            return self._send(200, {k: _state[k] for k in ("allowed", "blocked", "recent_blocks")}
                              | {"groups": len(policy().groups), "guests": len(policy().emails),
                                 "policy_updates": _state.get("policy_updates", 0)})
        if self.path == "/policy":
            return self._update_policy(raw)
        if self.path != "/proxy":
            return self._send(404, {"error": "not found"})
        req = json.loads(raw)
        service, method, path = req.get("service"), req.get("method", "GET").upper(), req.get("path", "")
        with _lock:
            p = policy()
            decision = p.check(service, method, path, req.get("params"), req.get("body"))
        entry = {"service": service, "method": method, "path": path,
                 "allowed": decision.allowed, "reason": decision.reason}
        if not decision.allowed:
            _state["blocked"] += 1
            _state["recent_blocks"] = ([{"reason": decision.reason, "at": time.time()}]
                                       + _state["recent_blocks"])[:10]
            audit(entry)
            return self._send(403, {"policy_gate": True, "error": decision.reason})
        try:
            result = gateway.call(method, _bases()[service], path,
                                  params=req.get("params"), body=req.get("body"))
        except gateway.GatewayError as exc:
            entry["reason"] = f"upstream error: {exc}"
            audit(entry)
            return self._send(502, {"error": str(exc)})
        with _lock:
            if decision.kind.startswith("write:"):
                p.record(decision.kind.split(":", 1)[1])
            if service == "telegram" and path.strip("/") == "getUpdates":
                p.observe_updates({"result": result} if isinstance(result, list) else result)
                _save_groups(p)
        if decision.kind != "read":
            _state["allowed"] += 1
            audit(entry)
        self._send(200, result)


def _policy_update(self, raw: bytes) -> None:
    """The host launched or switched an event: replace the email allowlist and
    connect code. Only the dashboard's launch button sends this, never the AI."""
    try:
        req = json.loads(raw)
        emails = sorted({str(e).strip().lower() for e in req.get("emails", []) if "@" in str(e)})[:300]
        code = str(req.get("host_code", "")).strip().upper()[:12]
    except (ValueError, AttributeError):
        return self._send(400, {"error": "bad request"})
    with _lock:
        data = json.loads(GATE_FILE.read_text()) if GATE_FILE.exists() else {}
        data.update({"inbox": data.get("inbox") or config.inbox_id(), "emails": emails, "host_code": code})
        GATE_FILE.write_text(json.dumps(data, indent=2))
        _state["policy_updates"] = _state.get("policy_updates", 0) + 1
    audit({"service": "policy", "method": "UPDATE", "path": str(req.get("event", ""))[:80],
           "allowed": True, "reason": f"host updated the guest list: {len(emails)} email(s)"})
    self._send(200, {"ok": True, "emails": len(emails)})


Handler._update_policy = _policy_update


def serve() -> None:
    port = config.dashboard_port()
    print(f"[gate] Policy gate listening on {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
