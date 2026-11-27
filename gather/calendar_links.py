"""Calendar invites: "Add to calendar" links Gus sends when a guest confirms.

Links (not .ics attachments) because they work in email and Telegram alike,
and the policy gate only lets plain text out.
"""
import datetime as dt
import re
from urllib.parse import urlencode

from . import events, outbox, store, themes

TZ_RE = re.compile(r"^[A-Za-z]+(?:/[A-Za-z0-9_+\-]+){1,2}$")


def start_end(event: dict):
    """(start, end) datetimes, or (date, None) for an all-day entry."""
    iso = events.iso_of(event)
    if not iso:
        return None
    day = dt.date.fromisoformat(iso)
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|am|pm)?", event.get("time") or "", re.I)
    if not m:
        return day, None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower().replace(".", "").replace(" ", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not ampm and 1 <= hour <= 7:
        hour += 12  # "dinner at 6" means the evening
    if hour > 23 or minute > 59:
        return day, None
    start = dt.datetime.combine(day, dt.time(hour, minute))
    return start, start + dt.timedelta(hours=3)


def links(event: dict, guest: dict | None = None) -> dict | None:
    se = start_end(event)
    if not se:
        return None
    start, end = se
    confirmed = guest and guest.get("status") == "attending"
    place = ((event.get("confirmed_only") or {}).get("address") if confirmed else None) or event.get("location") or ""
    title = event.get("name") or "Gathering"
    about = (f"{themes.label_for(event)} hosted by {event.get('host_name') or 'your host'}. "
             "Questions? Just message Uncle Gus.")
    if end:
        g_dates = f"{start:%Y%m%dT%H%M%S}/{end:%Y%m%dT%H%M%S}"
        o = {"startdt": f"{start:%Y-%m-%dT%H:%M:%S}", "enddt": f"{end:%Y-%m-%dT%H:%M:%S}", "allday": "false"}
    else:
        nxt = start + dt.timedelta(days=1)
        g_dates = f"{start:%Y%m%d}/{nxt:%Y%m%d}"
        o = {"startdt": start.isoformat(), "enddt": nxt.isoformat(), "allday": "true"}
    g = {"action": "TEMPLATE", "text": title, "dates": g_dates, "details": about, "location": place}
    if TZ_RE.match(event.get("timezone") or ""):
        g["ctz"] = event["timezone"]
    return {
        "google": "https://calendar.google.com/calendar/render?" + urlencode(g),
        "outlook": "https://outlook.live.com/calendar/0/action/compose?" + urlencode(
            {"subject": title, "body": about, "location": place, "path": "/calendar/action/compose",
             "rru": "addevent", **o}),
    }


def send_to(event: dict, guest: dict, channel: str | None = None) -> bool:
    """Send a confirmed guest their calendar links, once."""
    key = f"cal:{guest['id']}"
    if store.get_kv(key):
        return False
    lk = links(event, guest)
    if not lk:
        return False
    text = (f"📅 Pop {event.get('name')} in your calendar so nothing sneaks up on you:\n\n"
            f"Google Calendar: {lk['google']}\n\nOutlook / Hotmail: {lk['outlook']}\n\nUncle Gus")
    ok = outbox.send_to_guest(guest["id"], text, subject=f"Add to your calendar: {event.get('name')}",
                              channel=channel, kind="calendar")
    if ok:
        store.set_kv(key, "1")
    return ok
