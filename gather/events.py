"""Creating and launching events from the host dashboard.

Flow: create (draft) -> add guests -> launch. Launching makes the event the
one Uncle Gus works on, tells the policy gate the new guest list, and sends
the invitations in the background.

Only the host's dashboard (protected by the dashboard key) calls these.
Uncle Gus's AI has no way to call them, so a tricked agent can't add
someone to the guest list.
"""
import datetime as dt
import json
import re
import secrets
import threading
import time

from . import config, store, themes

EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
TEXT_LIMITS = {"name": 80, "time": 60, "location": 120, "address": 200, "details": 1500,
               "budget": 30, "host_name": 40}


class FormError(ValueError):
    pass


def _clean(form: dict, key: str) -> str:
    return str(form.get(key) or "").strip()[:TEXT_LIMITS.get(key, 200)]


def _nice_date(iso: str, with_year: bool = True) -> str:
    d = dt.date.fromisoformat(iso)
    return d.strftime("%A %-d %B %Y" if with_year else "%A %-d %B")


def event_data(form: dict, old: dict | None = None) -> dict:
    """Turn the New-event form into the event record (raises FormError)."""
    name = _clean(form, "name")
    if not name:
        raise FormError("Give the event a name.")
    etype = form.get("type") if form.get("type") in themes.THEMES else "dinner"
    type_label = str(form.get("type_label") or "").strip()[:40]
    if etype == "other" and not type_label:
        raise FormError("Say what kind of event it is (for example: baby shower).")
    date_iso = str(form.get("date_iso") or "").strip()
    try:
        nice = _nice_date(date_iso)
    except ValueError:
        raise FormError("Pick a date.")
    data = dict(old or {})
    data.update({
        "type": etype, "type_label": type_label if etype == "other" else "", "name": name, "date": nice, "date_iso": date_iso,
        "time": _clean(form, "time"), "location": _clean(form, "location"),
        "host_name": _clean(form, "host_name") or (old or {}).get("host_name") or config.host_name(),
        "budget": _clean(form, "budget"),
        "details": {"good to know": _clean(form, "details")} if _clean(form, "details") else {},
        "confirmed_only": {"address": _clean(form, "address")} if _clean(form, "address") else {},
    })
    rsvp = str(form.get("rsvp_iso") or "").strip()
    if rsvp:
        try:
            data["rsvp_by"], data["rsvp_iso"] = _nice_date(rsvp, with_year=False), rsvp
        except ValueError:
            raise FormError("The RSVP date isn't valid.")
    else:
        data.pop("rsvp_by", None), data.pop("rsvp_iso", None)
    tz = str(form.get("timezone") or "").strip()[:40]
    if re.fullmatch(r"[A-Za-z]+(?:/[A-Za-z0-9_+\-]+){1,2}", tz):
        data["timezone"] = tz
    food = str(form.get("food") or "").strip()
    data["food"] = food if food in ("home", "out", "catered") else data.get("food", "home")
    data.setdefault("status", "draft")
    data.setdefault("host_code", secrets.token_hex(3).upper())
    return data


def create(form: dict) -> int:
    event_id = store.create_event(event_data(form))
    store.activity("info", f"Draft event created: {form.get('name')}")
    return event_id


def update(event_id: int, form: dict) -> None:
    old = store.event_by_id(event_id)
    if not old:
        raise FormError("No such event.")
    new = event_data(form, {k: v for k, v in old.items() if k not in ("id", "created_at")})
    store.update_event(event_id, **new)


def add_guests(event_id: int, rows: list[dict]) -> list[str]:
    """Add guests; each row is {name, email?}. Returns problems, if any."""
    problems, have = [], {g["email"] for g in store.guests(event_id) if g["email"]}
    for row in rows[:200]:
        name = str(row.get("name") or "").strip()[:60]
        email = str(row.get("email") or "").strip().lower()[:120]
        if not name:
            problems.append("A guest needs a name.")
            continue
        if email and not EMAIL_RE.match(email):
            problems.append(f"{name}: '{email}' doesn't look like an email.")
            continue
        if email and email in have:
            problems.append(f"{email} is already on the list.")
            continue
        store.add_guest(event_id, name, email or None)
        have.add(email)
    return problems


def iso_of(event: dict) -> str:
    """The event date as YYYY-MM-DD, also for events made from event.json."""
    if event.get("date_iso"):
        return event["date_iso"]
    for fmt in ("%A %d %B %Y", "%d %B %Y", "%A, %d %B %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(str(event.get("date", "")).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def status_of(event: dict, guests: list[dict]) -> str:
    """draft / live / done, for the events list."""
    if event.get("status") == "done":
        return "done"
    try:
        if dt.date.fromisoformat(iso_of(event)) < dt.date.today():
            return "done"
    except ValueError:
        pass
    if event.get("status") == "live" or any(g["status"] != "new" for g in guests):
        return "live"
    return "draft"


def sync_gate(event: dict) -> str | None:
    """Tell the policy gate who may get email now. Returns an error, or None."""
    if not config.gate_url():
        return None
    import requests
    from . import gate_sign
    emails = sorted({g["email"] for g in store.guests(event["id"]) if g["email"]})
    body = json.dumps({"emails": emails, "host_code": event.get("host_code", ""),
                       "event": event.get("name", ""), "nonce": secrets.token_hex(8)}).encode()
    try:
        r = requests.post(config.gate_url() + "/policy", data=body, timeout=15,
                          headers=gate_sign.sign(config.gate_secret(), body))
        if not r.ok:
            return f"gate answered {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        return f"gate unreachable: {exc}"
    store.activity("gate", f"Policy gate now allows {len(emails)} guest email(s) for {event.get('name')}")
    return None


def launch(event_id: int) -> str | None:
    """Make this the active event, update the gate, send invitations. Returns an error or None."""
    event = store.event_by_id(event_id)
    if not event:
        return "No such event."
    if not store.guests(event_id):
        return "Add at least one guest first."
    err = sync_gate(event)
    if err:
        return f"Couldn't update the policy gate ({err}). Nothing was sent."
    store.update_event(event_id, status="live", launched_at=time.time())
    store.set_active_event(event_id)
    store.activity("info", f"Launched {event.get('name')}: Uncle Gus is sending the invitations")

    def send():
        from . import agent
        try:
            agent.invite_all()
        except Exception as exc:
            store.activity("info", f"Sending invitations failed: {exc}")
    threading.Thread(target=send, daemon=True).start()
    return None


def switch(event_id: int) -> str | None:
    """Point Uncle Gus at another (already launched) event."""
    event = store.event_by_id(event_id)
    if not event:
        return "No such event."
    if status_of(event, store.guests(event_id)) == "draft":
        return "Launch this event first."
    err = sync_gate(event)
    if err:
        return f"Couldn't update the policy gate ({err})."
    store.set_active_event(event_id)
    store.activity("info", f"Uncle Gus is now looking after {event.get('name')}")
    return None
