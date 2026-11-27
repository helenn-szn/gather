"""Secret Santa: Gus draws names among confirmed guests and tells each one
privately who they're buying for, plus that person's wishlist.

Nobody is told who is buying for them, and the dashboard hides the pairs
unless the host asks to see them.
"""
import json
import random
import time

from . import outbox, store

MIN_PLAYERS = 3


def _key(event_id: int) -> str:
    return f"santa:{event_id}"


def state(event: dict) -> dict | None:
    raw = store.get_kv(_key(event["id"]))
    return json.loads(raw) if raw else None


def _save(event: dict, st: dict | None) -> None:
    store.set_kv(_key(event["id"]), json.dumps(st) if st else "")


def players(event: dict) -> list[dict]:
    return [g for g in store.guests(event["id"])
            if g["status"] == "attending" and (g["email"] or g["telegram_chat_id"])]


def draw(event: dict, budget: str = "", note: str = "") -> str | None:
    """Draw names and tell everyone. Returns an error, or None."""
    if state(event):
        return "Names are already drawn for this event."
    ps = players(event)
    if len(ps) < MIN_PLAYERS:
        return f"Secret Santa needs at least {MIN_PLAYERS} confirmed guests Gus can reach (you have {len(ps)})."
    ids = [g["id"] for g in ps]
    random.SystemRandom().shuffle(ids)
    pairs = {str(a): b for a, b in zip(ids, ids[1:] + ids[:1])}  # one loop: nobody draws themselves
    st = {"pairs": pairs, "budget": budget.strip()[:40], "note": note.strip()[:300],
          "drawn_at": time.time(), "told": []}
    _save(event, st)
    store.activity("santa", f"Secret Santa drawn for {len(ids)} guests")
    for giver in ids:
        if _tell(event, giver, st):
            st["told"].append(giver)
    _save(event, st)
    return None


def _wish_line(receiver: dict) -> str:
    if receiver.get("wishlist"):
        return f"Their wishlist: {receiver['wishlist']}"
    return "They haven't shared a wishlist yet. I'll whisper it to you if they do."


def _tell(event: dict, giver_id: int, st: dict) -> bool:
    receiver = store.guest_by_id(st["pairs"][str(giver_id)])
    lines = [f"🎁 Secret Santa for {event.get('name')}! You're buying a gift for {receiver['name']}."]
    if st.get("budget"):
        lines.append(f"Budget: {st['budget']}.")
    lines.append(_wish_line(receiver))
    if st.get("note"):
        lines.append(st["note"])
    lines.append("Keep it secret! And tell me your own wishlist anytime; I'll pass it on quietly.\n\nUncle Gus")
    return outbox.send_to_guest(giver_id, "\n".join(lines), subject=f"Your Secret Santa for {event.get('name')} 🎁",
                                kind="secret santa")


def tell_missing(event: dict) -> int:
    """Re-send to players who weren't reached the first time."""
    st = state(event)
    if not st:
        return 0
    n = 0
    for giver in map(int, st["pairs"]):
        if giver not in st["told"] and _tell(event, giver, st):
            st["told"].append(giver)
            n += 1
    _save(event, st)
    return n


def wishlist_updated(event: dict, guest: dict) -> None:
    """A guest shared a wishlist: quietly pass it to whoever is buying for them."""
    st = state(event)
    if not st:
        return
    giver = next((int(a) for a, b in st["pairs"].items() if b == guest["id"]), None)
    if giver:
        outbox.send_to_guest(giver, f"Psst! {guest['name']} just shared their wishlist: {guest['wishlist']}\n\nUncle Gus",
                             subject="Secret Santa: a wishlist arrived 🎁", kind="secret santa")


def facts_for_guest(event: dict, guest: dict) -> dict | None:
    """What Gus may tell this guest about Secret Santa (only their own side)."""
    st = state(event)
    if not st or str(guest["id"]) not in st["pairs"]:
        return None
    receiver = store.guest_by_id(st["pairs"][str(guest["id"])])
    return {"you_are_buying_for": receiver["name"], "their_wishlist": receiver.get("wishlist") or "not shared yet",
            "budget": st.get("budget") or "no budget set",
            "rule": "Private. Never say who is buying for this guest, or who anyone else drew."}


def summary(event: dict) -> dict:
    st = state(event)
    ps = players(event)
    out = {"eligible": len(ps), "min": MIN_PLAYERS, "drawn": bool(st)}
    if st:
        ids = [int(a) for a in st["pairs"]]
        guests = {g["id"]: g for g in store.guests(event["id"])}
        out.update({"budget": st.get("budget"), "note": st.get("note"), "drawn_at": st["drawn_at"],
                    "players": [{"name": guests[i]["name"] if i in guests else "?", "told": i in st["told"],
                                 "wishlist": bool(guests.get(i, {}).get("wishlist"))} for i in ids],
                    "pairs": [{"giver": guests.get(int(a), {}).get("name", "?"), "receiver": guests.get(b, {}).get("name", "?")}
                              for a, b in st["pairs"].items()]})
    return out


def reset(event: dict) -> None:
    _save(event, None)
    store.activity("santa", "Secret Santa reset by the host")
