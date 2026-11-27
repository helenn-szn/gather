"""Photo collection after the event.

Gus asks confirmed guests for their photos. Photos emailed to Gus are saved
on the Sprite's disk and shown on the dashboard. Photos sent on Telegram are
copied into the event's group, so everyone sees one shared album.
"""
import json
import re
import secrets
import time

import requests

from . import config, gateway, outbox, store
from .config import ROOT

PHOTO_DIR = ROOT / "photos"
MAX_BYTES = 15 * 1024 * 1024
EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/gif": "gif",
       "image/webp": "webp", "image/heic": "heic", "image/heif": "heic"}
FILE_RE = re.compile(r"^\d+-[0-9a-f]{8}\.(jpg|png|gif|webp|heic)$")


def _url_ok(url: str) -> bool:
    return url.startswith("https://")


def _key(event_id: int) -> str:
    return f"photos:{event_id}"


def all_photos(event: dict) -> list[dict]:
    raw = store.get_kv(_key(event["id"]))
    return json.loads(raw) if raw else []


def _add(event: dict, record: dict) -> None:
    items = all_photos(event)
    items.append({**record, "at": time.time()})
    store.set_kv(_key(event["id"]), json.dumps(items[-500:]))


def ask_guests(event: dict) -> int:
    """Ask every confirmed guest for photos, once each. Returns how many were asked."""
    n = 0
    for g in store.guests(event["id"]):
        if g["status"] != "attending" or store.get_kv(f"photoask:{g['id']}"):
            continue
        text = (f"📸 What a gathering! Could you send me your favourite photos from {event.get('name')}? "
                "Just reply with them attached (or send them to me here on Telegram). "
                f"I'm putting everyone's into one album for {event.get('host_name') or 'the host'}.\n\nUncle Gus")
        if outbox.send_to_guest(g["id"], text, subject=f"Your photos from {event.get('name')} 📸", kind="photos"):
            store.set_kv(f"photoask:{g['id']}", "1")
            n += 1
    store.activity("photo", f"Asked {n} guest(s) for their photos")
    return n


def save_email_photos(event: dict, guest: dict, message_id: str, attachments: list[dict]) -> int:
    """Download image attachments from a guest's email. Returns how many were saved."""
    saved = 0
    folder = PHOTO_DIR / str(event["id"])
    for att in attachments[:12]:
        ctype = str(att.get("content_type") or "").lower()
        if ctype not in EXT or int(att.get("size") or 0) > MAX_BYTES or not att.get("attachment_id"):
            continue
        try:
            meta = gateway.call("GET", config.agentmail_base(),
                                f"/v0/inboxes/{gateway.quote(config.inbox_id())}/messages/"
                                f"{gateway.quote(message_id)}/attachments/{gateway.quote(att['attachment_id'])}")
            url = meta.get("download_url", "")
            if not _url_ok(url):
                continue
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > MAX_BYTES:
                    raise ValueError("photo too large")
        except Exception as exc:
            store.activity("info", f"Couldn't save a photo from {guest['name']}: {exc}")
            continue
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{guest['id']}-{secrets.token_hex(4)}.{EXT[ctype]}"
        (folder / name).write_bytes(data)
        _add(event, {"source": "email", "file": name, "guest": guest["name"]})
        saved += 1
    if saved:
        store.activity("photo", f"Saved {saved} photo(s) from {guest['name']}")
    return saved


def save_telegram_photo(event: dict, guest: dict | None, msg: dict, in_group: bool) -> bool:
    """Record a Telegram photo; copy private ones into the event group. Returns True if copied."""
    who = guest["name"] if guest else (msg.get("from") or {}).get("first_name", "someone")
    copied = False
    if not in_group:
        from . import groups
        from .channels import telegram
        info = groups.group(event)
        if info:
            try:
                telegram.copy_message(info["chat_id"], msg["chat"]["id"], msg["message_id"])
                copied = True
            except Exception as exc:
                store.activity("info", f"Couldn't add {who}'s photo to the group: {exc}")
    _add(event, {"source": "telegram-group" if in_group else "telegram", "guest": who, "copied": copied})
    store.activity("photo", f"{who} shared a photo on Telegram")
    return copied


def file_path(event_id: int, name: str):
    if not FILE_RE.match(name or ""):
        return None
    p = PHOTO_DIR / str(event_id) / name
    return p if p.exists() else None


def summary(event: dict) -> dict:
    items = all_photos(event)
    return {"email": [{"file": p["file"], "guest": p["guest"], "at": p["at"]} for p in items if p["source"] == "email"],
            "telegram": sum(1 for p in items if p["source"].startswith("telegram")),
            "asked": sum(1 for g in store.guests(event["id"]) if store.get_kv(f"photoask:{g['id']}"))}
