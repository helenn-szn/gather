"""Telegram group for the event.

Bots can't create Telegram groups, so the host does one step: add the bot to
a new or existing group as an admin (with "Invite users"), then send
`/connect CODE` there (CODE comes from setup_event). After that Gus:

  * gives every confirmed guest their OWN invite link. Links only *request*
    to join; Gus approves a request only if the link belongs to a confirmed
    guest and hasn't been used before. A forwarded link gets declined.
  * answers questions in the group when someone mentions him or replies to
    him, using only public event details (never the private address).
  * posts the host's announcements and pins them.
"""
import json

from . import config, llm, outbox, potluck, store, uncle
from .channels import telegram

LINK_PREFIX = "gather-"


def group(event: dict) -> dict | None:
    raw = store.get_kv(f"group:{event['id']}")
    return json.loads(raw) if raw else None


def _bot_username() -> str:
    from . import agent
    return (agent.bot_username() or "").lower()


# ---------- connecting ----------
def handle_bot_added(event: dict, chat: dict) -> None:
    if group(event) and group(event)["chat_id"] == str(chat["id"]):
        return
    telegram.send(chat["id"], "Hello! I'm Uncle Gus. To use this group for your event, make me an "
                  "admin with 'Invite users' permission, then the host sends /connect followed "
                  "by their code.")
    store.activity("info", f"Added to Telegram group '{chat.get('title', '')}', waiting for /connect")


def connect(event: dict, chat: dict, code: str) -> None:
    if not code or code.strip().upper() != str(event.get("host_code", "")).upper():
        telegram.send(chat["id"], "That code doesn't match. The host's code is shown when the "
                      "event is created, and on the dashboard.")
        store.flag("blocked", f"Wrong /connect code tried in Telegram group '{chat.get('title', '')}'")
        return
    info = {"chat_id": str(chat["id"]), "title": chat.get("title", "Event group"),
            "approved": 0, "declined": 0}
    store.set_kv(f"group:{event['id']}", json.dumps(info))
    telegram.send(chat["id"], f"Connected! This is now the group for {event.get('name')}. "
                  "I'll let in confirmed guests, answer questions (mention me) and post updates.")
    store.activity("group", f"Telegram group '{info['title']}' connected")
    for g in store.guests(event["id"]):
        share_link(event, g)


# ---------- invite links ----------
def share_link(event: dict, guest: dict) -> None:
    """Send a confirmed guest their own join link, once."""
    info = group(event)
    if not info or guest["status"] != "attending" or guest["group_link_sent"] or guest["group_joined"]:
        return
    try:
        link = telegram.create_invite_link(info["chat_id"], LINK_PREFIX + guest["invite_code"])
    except Exception as exc:
        store.flag("blocked", f"Couldn't create a group link for {guest['name']}: {exc}. "
                   "Is Gus an admin with 'Invite users'?", guest["id"])
        return
    store.update_guest(guest["id"], group_link=link)
    text = (f"You're in the group chat for {event.get('name')}! Here's your personal link: {link}\n"
            "It only works for you, so please don't forward it.\n\nUncle Gus")
    if outbox.send_to_guest(guest["id"], text, subject=f"Your group chat for {event.get('name')}",
                            kind="group link"):
        store.update_guest(guest["id"], group_link_sent=1)


def handle_join_request(event: dict, req: dict) -> None:
    info = group(event)
    chat_id, user = str(req["chat"]["id"]), req["from"]
    if not info or chat_id != info["chat_id"]:
        return
    name = (req.get("invite_link") or {}).get("name", "")
    code = name[len(LINK_PREFIX):] if name.startswith(LINK_PREFIX) else ""
    guest = store.guest_by_code(event["id"], code) if code else None
    who = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "someone"

    ok = guest and guest["status"] == "attending" and not guest["group_joined"]
    if ok:
        telegram.approve_join(chat_id, user["id"])
        store.update_guest(guest["id"], group_joined=1, telegram_user_id=str(user["id"]))
        info["approved"] += 1
        store.activity("group", f"Let {guest['name']} into the group")
    else:
        telegram.decline_join(chat_id, user["id"])
        info["declined"] += 1
        reason = ("used a link that was already used" if guest and guest["group_joined"]
                  else "isn't a confirmed guest" if guest else "had no personal invite link")
        store.flag("blocked", f"Declined {who}'s request to join the group: {reason}.")
    store.set_kv(f"group:{event['id']}", json.dumps(info))


# ---------- the group conversation ----------
# Words that suggest a message is worth reading even when Gus isn't mentioned.
RSVP_HINTS = ("bring", "bringing", "allerg", "vegetarian", "vegan", "halal", "kosher", "gluten",
              "coming", "can't make", "cant make", "won't make", "plus one", "+1")


def handle_group_message(event: dict, msg: dict) -> None:
    info = group(event)
    if not info or str(msg["chat"]["id"]) != info["chat_id"]:
        return
    text = msg.get("text", "")
    me = _bot_username()
    mentioned = bool(me) and f"@{me}" in text.lower()
    replied = ((msg.get("reply_to_message") or {}).get("from") or {}).get("username", "").lower() == me
    sender = msg.get("from", {})
    name = sender.get("first_name") or "friend"
    guest = store.guest_by_telegram_user(event["id"], str(sender.get("id", "")))

    if not (mentioned or replied):
        # Quietly keep the guest list current when a guest mentions what they're bringing etc.
        if guest and any(h in text.lower() for h in RSVP_HINTS):
            parsed = llm.parse_guest_reply(guest["name"], text, event.get("name", ""))
            if not parsed["suspicious"]:
                _note_guest_update(event, guest, parsed, msg)
        return

    parsed = llm.parse_guest_reply(name, text, event.get("name", ""))
    if parsed["suspicious"]:
        store.flag("suspicious", f"A group message from {name} tried to give Gus instructions "
                   f"({parsed.get('suspicious_reason') or 'unclear'}). Not acted on.")
        outbox.send_to_group(event, "I only help with questions about the event. Anything else "
                             "goes to the host.", reply_to=msg.get("message_id"))
        return
    nudge = _note_guest_update(event, guest, parsed, msg, speak=False) if guest else None
    facts = uncle.facts_for(event, None)  # public details only, never the address
    facts["note"] = "This is the group chat: share nothing private; personal details go by DM."
    out = llm.compose_reply(facts, name, parsed, text, [], group_chat=True)
    for q in out["for_host"]:
        store.flag("question", f"{name} asks in the group: {q}")
    answer = out["reply"] + (f"\n\n{nudge}" if nudge else "")
    outbox.send_to_group(event, answer, reply_to=msg.get("message_id"))
    store.activity("answer", f"Answered {name} in the group")


def _note_guest_update(event: dict, guest: dict, parsed: dict, msg: dict, speak: bool = True):
    """Apply a guest's group message to the guest list; maybe nudge about duplicates."""
    before = guest.get("bringing")
    changes = uncle.apply_updates(guest, parsed, source=" (in the group)")
    nudge = None
    if changes.get("bringing") and changes["bringing"] != before:
        nudge = potluck.check(event, guest)
        if nudge and speak:
            outbox.send_to_group(event, nudge, reply_to=msg.get("message_id"))
    return nudge


# ---------- announcements ----------
def announce(event: dict, text: str, pin_it: bool = True) -> bool:
    info = group(event)
    if not info:
        return False
    sent = outbox.send_to_group(event, f"📣 {text.strip()}\n\nUncle Gus", pin_it=pin_it)
    if sent:
        store.activity("group", "Posted an update to the group")
    return sent
