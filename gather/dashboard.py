"""Live host dashboard, served from the Sprite on port 8080.

Open it at https://<your-sprite-url>/?key=<DASHBOARD_KEY>
Every request needs the key, because the page shows guest names and emails.
Guest-written text is inserted with textContent in the browser, never as HTML.
"""
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import agent, config, groups, store, themes

PAGE = (Path(__file__).parent / "dashboard.html").read_text()


_gate_cache = {"at": 0.0, "data": None}


def gate_stats() -> dict | None:
    """Allowed/blocked counts from the policy gate (cached for 10 seconds)."""
    if not config.gate_url():
        return None
    if time.time() - _gate_cache["at"] > 10:
        try:
            import requests
            from . import gate_sign
            import secrets
            body = json.dumps({"nonce": secrets.token_hex(8)}).encode()
            r = requests.post(config.gate_url() + "/stats", data=body, timeout=5,
                              headers=gate_sign.sign(config.gate_secret(), body))
            _gate_cache["data"] = r.json() if r.ok else {"error": f"gate answered {r.status_code}"}
        except Exception as exc:
            _gate_cache["data"] = {"error": f"gate unreachable: {exc.__class__.__name__}"}
        _gate_cache["at"] = time.time()
    return _gate_cache["data"]


def state() -> dict:
    event = store.current_event()
    if not event:
        return {"event": None}
    guests = store.guests(event["id"])
    counts = {s: 0 for s in ("new", "invited", "attending", "maybe", "declined")}
    for g in guests:
        counts[g["status"]] = counts.get(g["status"], 0) + 1
    dietary: dict[str, int] = {}
    for g in guests:
        if g["status"] == "attending":
            for d in g["dietary"]:
                dietary[d] = dietary.get(d, 0) + 1
    grp = groups.group(event)
    return {
        "event": {k: event.get(k) for k in
                  ("name", "host_name", "date", "time", "location", "budget", "rsvp_by")},
        "theme": themes.get(event.get("type")),
        "host_code": event.get("host_code"),
        "bot": agent.bot_username(),
        "gate": gate_stats(),
        "group": grp and {"title": grp["title"], "approved": grp["approved"],
                          "declined": grp["declined"],
                          "joined": sum(1 for g in guests if g["group_joined"])},
        "counts": counts,
        "headcount": sum(g["party_size"] for g in guests if g["status"] == "attending"),
        "dietary": dietary,
        "guests": [{
            "name": g["name"], "email": g["email"], "status": g["status"],
            "channel": "telegram" if g["telegram_chat_id"] else ("email" if g["email"] else "none"),
            "party_size": g["party_size"], "dietary": g["dietary"], "bringing": g["bringing"],
            "followups": min(g["followups_sent"], config.max_followups()), "telegram_link": agent.telegram_link(g),
            "last_heard": g["last_inbound_at"],
            "in_group": bool(g["group_joined"]),
        } for g in guests],
        "flags": store.open_flags(),
        "activity": store.recent_activity(),
        "now": time.time(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the service log quiet
        pass

    def _authorized(self) -> bool:
        qs = parse_qs(urlparse(self.path).query)
        given = (qs.get("key") or [""])[0] or self.headers.get("X-Dashboard-Key", "")
        return hmac.compare_digest(given.encode(), config.dashboard_key().encode())

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._authorized():
            return self._send(401, "<h1>Uncle Gus says: you need the dashboard key.</h1>"
                                   "<p>Open this page as <code>/?key=YOUR_DASHBOARD_KEY</code>.</p>")
        if path == "/":
            return self._send(200, PAGE)
        if path == "/api/state":
            return self._send(200, json.dumps(state()), "application/json")
        self._send(404, "Not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._authorized():
            return self._send(401, "{}", "application/json")
        if path == "/api/announce":
            length = min(int(self.headers.get("Content-Length") or 0), 4000)
            try:
                text = (json.loads(self.rfile.read(length) or b"{}").get("text") or "").strip()
            except ValueError:
                text = ""
            event = store.current_event()
            ok = bool(text and event and groups.announce(event, text[:1500]))
            return self._send(200 if ok else 400, json.dumps({"ok": ok}), "application/json")
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "flags"] and parts[3] == "resolve":
            store.resolve_flag(int(parts[2]))
            return self._send(200, "{}", "application/json")
        self._send(404, "{}", "application/json")


def serve() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", config.dashboard_port()), Handler)
    store.activity("info", f"Dashboard running on port {config.dashboard_port()}")
    server.serve_forever()
