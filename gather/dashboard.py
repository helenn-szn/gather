"""Live host dashboard, served from the Sprite on port 8080.

Open it at https://<your-sprite-url>/?key=<DASHBOARD_KEY>
Every request needs the key, because the page shows guest names and emails.
Guest-written text is inserted with textContent in the browser, never as HTML.

The host can also create events, add guests and launch from here
(see gather/events.py). Uncle Gus's AI can't reach these endpoints.
"""
import hmac
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import agent, budget, calendar_links, config, cooking, events, groups, photos, potluck, santa, store, themes

PAGE_FILE = Path(__file__).parent / "dashboard.html"
_gate_cache = {"at": 0.0, "data": None}


def gate_stats() -> dict | None:
    """Allowed/blocked counts from the policy gate (cached for 10 seconds)."""
    if not config.gate_url():
        return None
    if time.time() - _gate_cache["at"] > 10:
        try:
            import requests
            from . import gate_sign
            body = json.dumps({"nonce": secrets.token_hex(8)}).encode()
            r = requests.post(config.gate_url() + "/stats", data=body, timeout=5,
                              headers=gate_sign.sign(config.gate_secret(), body))
            _gate_cache["data"] = r.json() if r.ok else {"error": f"gate answered {r.status_code}"}
        except Exception as exc:
            _gate_cache["data"] = {"error": f"gate unreachable: {exc.__class__.__name__}"}
        _gate_cache["at"] = time.time()
    return _gate_cache["data"]


def _summary(ev: dict, active_id: int | None) -> dict:
    gs = store.guests(ev["id"])
    return {"id": ev["id"], "name": ev.get("name"), "type": ev.get("type") or "dinner",
            "type_label": themes.label_for(ev),
            "date": ev.get("date"), "date_iso": events.iso_of(ev), "time": ev.get("time"),
            "location": ev.get("location"), "status": events.status_of(ev, gs),
            "active": ev["id"] == active_id, "invited": len(gs),
            "coming": sum(1 for g in gs if g["status"] == "attending"),
            "headcount": sum(g["party_size"] for g in gs if g["status"] == "attending")}


def _upcoming(event: dict, guests: list[dict]) -> list[dict]:
    out = []
    quiet = [g for g in guests if g["status"] == "invited" and not g["last_inbound_at"]
             and g["followups_sent"] < config.max_followups()]
    if quiet:
        wait = config.followup_after_minutes() * 60
        due = min((g["last_outbound_at"] or g["invited_at"] or time.time()) + wait for g in quiet)
        out.append({"at": max(due, time.time()), "text": f"Gus nudges {len(quiet)} quiet guest(s)",
                    "sub": "Automatic, by email or Telegram"})
    for key, text, sub in (("rsvp_iso", "RSVPs close", "Gus sends you the final list"),
                           ("date_iso", event.get("name") or "The event", event.get("time") or "")):
        if key == "date_iso" and not event.get(key) and events.iso_of(event):
            event = {**event, "date_iso": events.iso_of(event)}
        if event.get(key):
            try:
                out.append({"at": time.mktime(time.strptime(event[key], "%Y-%m-%d")), "text": text, "sub": sub})
            except ValueError:
                pass
    return sorted(out, key=lambda x: x["at"])


def state(event_id: int | None = None) -> dict:
    active = store.current_event()
    active_id = active["id"] if active else None
    all_events = [_summary(e, active_id) for e in store.list_events()]
    event = store.event_by_id(event_id) if event_id else active
    base = {"events": all_events, "active_id": active_id, "bot": agent.bot_username(),
            "gate": gate_stats(), "flags": store.open_flags(), "activity": store.recent_activity(60),
            "themes": {k: {"label": t["label"], "colors": t["colors"], "accessory": t["accessory"],
                           "emoji": t["emoji"]} for k, t in themes.THEMES.items()},
            "host_name": config.host_name(), "now": time.time()}
    if not event:
        return base | {"event": None}
    guests = store.guests(event["id"])
    counts = {s: 0 for s in ("new", "invited", "attending", "maybe", "declined")}
    dietary: dict[str, int] = {}
    food: dict[str, list] = {}
    for g in guests:
        counts[g["status"]] = counts.get(g["status"], 0) + 1
        if g["status"] == "attending":
            for d in g["dietary"]:
                dietary[d] = dietary.get(d, 0) + 1
        if g["bringing"]:
            food.setdefault(g.get("bring_category") or "other", []).append(
                {"who": g["name"], "item": g["bringing"]})
    grp = groups.group(event)
    return base | {
        "event": {k: event.get(k) for k in ("id", "name", "host_name", "date", "date_iso", "time",
                                             "location", "budget", "rsvp_by", "rsvp_iso", "type", "type_label")}
                 | {"food": event.get("food", "home")}
                 | {"details": (event.get("details") or {}).get("good to know", ""),
                    "address": (event.get("confirmed_only") or {}).get("address", ""),
                    "status": events.status_of(event, guests), "active": event["id"] == active_id,
                    "date_iso": events.iso_of(event)},
        "theme": themes.get(event.get("type")) | {"key": themes.get(event.get("type"))["key"],
                                                   "label": themes.label_for(event)},
        "host_code": event.get("host_code"),
        "group": grp and {"title": grp["title"], "approved": grp["approved"],
                          "declined": grp["declined"],
                          "joined": sum(1 for g in guests if g["group_joined"])},
        "counts": counts,
        "headcount": sum(g["party_size"] for g in guests if g["status"] == "attending"),
        "dietary": dietary,
        "food": food, "food_limits": potluck.LIMITS,
        "upcoming": _upcoming(event, guests),
        "santa": santa.summary(event),
        "cooking": cooking.get(event),
        "serve_default": _serve_default(event),
        "budget": budget.summary(event, sum(g["party_size"] for g in guests if g["status"] == "attending")),
        "expected": _expected(guests),
        "food_plan": budget.plan_summary(event, _expected(guests), dietary),
        "photos": photos.summary(event),
        "calendar": calendar_links.links(event),
        "guests": [{
            "id": g["id"], "name": g["name"], "email": g["email"], "status": g["status"],
            "channel": "telegram" if g["telegram_chat_id"] else ("email" if g["email"] else "none"),
            "party_size": g["party_size"], "dietary": g["dietary"], "bringing": g["bringing"],
            "followups": min(g["followups_sent"], config.max_followups()),
            "telegram_link": agent.telegram_link(g), "last_heard": g["last_inbound_at"],
            "in_group": bool(g["group_joined"]),
        } for g in guests],
    }


def _expected(guests: list[dict]) -> int:
    """People to plan for: confirmed (with plus-ones), plus anyone not yet answered, plus the host."""
    return (sum(g["party_size"] for g in guests if g["status"] == "attending")
            + sum(1 for g in guests if g["status"] in ("new", "invited", "maybe")) + 1)


def _serve_default(event: dict) -> str:
    se = calendar_links.start_end(event)
    if se and se[1]:
        start = se[0]
        return f"{(start.hour + 1) % 24:02d}:{start.minute:02d}"
    return "19:00"


def _photo_zip(event: dict) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for p in photos.summary(event)["email"]:
            path = photos.file_path(event["id"], p["file"])
            if path:
                z.write(path, f"{p['guest']}-{p['file']}")
    return buf.getvalue()


def remembered(event_id: int) -> list[dict]:
    """What Gus remembers about this event's guests from earlier events (for the review step)."""
    from . import memory
    event = store.event_by_id(event_id)
    out = []
    for g in store.guests(event_id)[:20]:
        notes = memory.recall(event, g, "dietary needs, what they brought, preferences", k=2)
        if notes:
            out.append({"name": g["name"], "notes": notes})
    return out


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

    def _bytes(self, data: bytes, ctype: str, filename: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj), "application/json")

    def _body(self) -> dict:
        length = min(int(self.headers.get("Content-Length") or 0), 64000)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def do_GET(self):
        url = urlparse(self.path)
        if not self._authorized():
            return self._send(401, "<h1>Uncle Gus says: you need the dashboard key.</h1>"
                                   "<p>Open this page as <code>/?key=YOUR_DASHBOARD_KEY</code>.</p>")
        if url.path == "/":
            return self._send(200, PAGE_FILE.read_text())
        if url.path == "/api/state":
            ev = (parse_qs(url.query).get("event") or [""])[0]
            return self._json(200, state(int(ev) if ev.isdigit() else None))
        parts = url.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "events"] and parts[3] == "remembered" and parts[2].isdigit():
            return self._json(200, {"remembered": remembered(int(parts[2]))})
        if len(parts) == 3 and parts[0] == "photos" and parts[1].isdigit():
            path = photos.file_path(int(parts[1]), parts[2])
            if path:
                ext = path.suffix.lstrip(".")
                return self._bytes(path.read_bytes(), {"jpg": "image/jpeg", "heic": "image/heic"}.get(ext, f"image/{ext}"))
        if len(parts) == 4 and parts[:2] == ["api", "events"] and parts[3] == "photos.zip" and parts[2].isdigit():
            ev = store.event_by_id(int(parts[2]))
            if ev:
                return self._bytes(_photo_zip(ev), "application/zip", f"gather-photos-{ev['id']}.zip")
        self._send(404, "Not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._authorized():
            return self._json(401, {})
        body = self._body()
        parts = path.strip("/").split("/")
        try:
            if path == "/api/announce":
                text = str(body.get("text") or "").strip()
                event = store.current_event()
                ok = bool(text and event and groups.announce(event, text[:1500]))
                return self._json(200 if ok else 400, {"ok": ok})
            if len(parts) == 4 and parts[:2] == ["api", "flags"] and parts[3] == "resolve":
                store.resolve_flag(int(parts[2]))
                return self._json(200, {"ok": True})
            if path == "/api/events":
                return self._json(200, {"ok": True, "id": events.create(body)})
            if len(parts) >= 3 and parts[:2] == ["api", "events"] and parts[2].isdigit():
                eid = int(parts[2])
                action = parts[3] if len(parts) > 3 else "update"
                if action == "update":
                    events.update(eid, body)
                    return self._json(200, {"ok": True})
                if action == "guests" and len(parts) == 4:
                    rows = body.get("guests") if isinstance(body.get("guests"), list) else [body]
                    return self._json(200, {"ok": True, "problems": events.add_guests(eid, rows)})
                if action == "guests" and len(parts) == 6 and parts[5] == "remove":
                    g = store.guest_by_id(int(parts[4]))
                    if not g or g["event_id"] != eid:
                        return self._json(404, {"error": "No such guest."})
                    if g["status"] != "new":
                        return self._json(400, {"error": "Gus has already invited this guest."})
                    store.remove_guest(g["id"])
                    return self._json(200, {"ok": True})
                event = store.event_by_id(eid)
                if not event:
                    return self._json(404, {"error": "No such event."})
                is_active = (store.current_event() or {}).get("id") == eid
                if action in ("santa", "photos") and not is_active:
                    return self._json(400, {"error": "Make this Uncle Gus's current event first."})
                if action == "santa":
                    sub = parts[4] if len(parts) > 4 else "draw"
                    if sub == "draw":
                        err = santa.draw(event, str(body.get("budget") or ""), str(body.get("note") or ""))
                        return self._json(400 if err else 200, {"ok": not err, "error": err})
                    if sub == "retell":
                        return self._json(200, {"ok": True, "sent": santa.tell_missing(event)})
                    if sub == "reset":
                        santa.reset(event)
                        return self._json(200, {"ok": True})
                if action == "cooking":
                    if len(parts) > 4 and parts[4] == "toggle":
                        cooking.toggle(event, int(body.get("i", -1)))
                        return self._json(200, {"ok": True})
                    try:
                        return self._json(200, {"ok": True, "plan": cooking.make_plan(
                            event, str(body.get("dishes") or ""), str(body.get("serve_at") or ""))})
                    except ValueError as exc:
                        return self._json(400, {"error": str(exc)})
                if action == "food":
                    f = str(body.get("food") or "")
                    if f not in ("home", "out", "catered"):
                        return self._json(400, {"error": "Pick home, out or catered."})
                    store.update_event(eid, food=f)
                    return self._json(200, {"ok": True})
                if action == "budget":
                    sub = parts[4] if len(parts) > 4 else "add"
                    try:
                        if sub == "add":
                            budget.add(event, body.get("what"), body.get("amount"), str(body.get("category") or ""))
                        elif sub == "remove":
                            budget.remove(event, str(body.get("id") or ""))
                        elif sub == "total":
                            budget.set_total(event, body.get("total"))
                        elif sub == "plan":
                            gs = store.guests(eid)
                            diet: dict[str, int] = {}
                            for g in gs:
                                if g["status"] == "attending":
                                    for d in g["dietary"]:
                                        diet[d] = diet.get(d, 0) + 1
                            n = budget.parse_amount(body.get("people"))
                            budget.make_plan(event, int(n) if n is not None else _expected(gs), diet,
                                             [g["bringing"] for g in gs if g["bringing"] and g["status"] == "attending"],
                                             str(body.get("wishes") or ""))
                        elif sub == "shop":
                            budget.mark_bought(event, str(body.get("id") or ""), bool(body.get("bought")), body.get("actual"))
                    except ValueError as exc:
                        return self._json(400, {"error": str(exc)})
                    return self._json(200, {"ok": True})
                if action == "photos" and len(parts) > 4 and parts[4] == "ask":
                    return self._json(200, {"ok": True, "asked": photos.ask_guests(event)})
                if action in ("launch", "switch"):
                    err = (events.launch if action == "launch" else events.switch)(eid)
                    return self._json(400 if err else 200, {"ok": not err, "error": err})
        except events.FormError as exc:
            return self._json(400, {"error": str(exc)})
        except (ValueError, KeyError) as exc:
            return self._json(400, {"error": f"Bad request: {exc}"})
        self._json(404, {"error": "not found"})


def serve() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", config.dashboard_port()), Handler)
    store.activity("info", f"Dashboard running on port {config.dashboard_port()}")
    server.serve_forever()
