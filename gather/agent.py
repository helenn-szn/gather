"""Uncle Gus's main loop: invite, listen, answer, chase.

One `tick()` checks email, checks Telegram, then sends any follow-ups due.
`scripts/run_agent.py` calls it forever as a Sprite service.
"""
import time

from . import config, groups, llm, memory, outbox, potluck, store, uncle
from .channels import IncomingMessage, email, telegram

_bot_username: str | None = None


def bot_username() -> str | None:
    global _bot_username
    if _bot_username is None:
        _bot_username = config.telegram_bot_username()
        if not _bot_username:
            try:
                _bot_username = telegram.whoami().get("username", "")
            except Exception as exc:  # Telegram is optional
                store.activity("info", f"Couldn't look up the Telegram bot: {exc}")
                _bot_username = ""
    return _bot_username or None


def telegram_link(guest: dict) -> str | None:
    name = bot_username()
    return f"https://t.me/{name}?start={guest['invite_code']}" if name else None


# ---------- invitations ----------
def invite_all() -> None:
    event = store.current_event()
    if not event:
        print("No event yet. Run: python3 -m scripts.setup_event event.json")
        return
    for guest in store.guests(event["id"]):
        if guest["status"] != "new":
            continue
        link = telegram_link(guest)
        if not guest["email"]:
            store.activity("info", f"{guest['name']} has no email. Share their Telegram link: {link}")
            continue
        remembered = memory.recall(event, guest, "dietary needs, what they brought, preferences")
        if remembered:
            store.activity("memory", f"Remembered {guest['name']} from a past event")
        text = llm.write_invitation(uncle.facts_for(event, guest), guest["name"], link, remembered)
        subject = f"You're invited: {event.get('name', 'a gathering')} 🎄"
        if outbox.send_to_guest(guest["id"], text, subject=subject, kind="invitation"):
            store.update_guest(guest["id"], status="invited", invited_at=time.time())


# ---------- incoming ----------
def _reply(guest: dict, msg: IncomingMessage, event: dict) -> None:
    was = guest["status"]
    text = uncle.handle_guest_message(event, guest, msg)
    if msg.channel == "telegram":
        outbox.send_to_guest(guest["id"], text, channel="telegram", kind="reply")
    else:
        outbox.send_to_guest(guest["id"], text, reply_ref=msg.reply_ref,
                             channel="email", kind="reply")
    now = store.guest_by_id(guest["id"])
    if now.get("bringing") and now["bringing"] != guest.get("bringing"):
        nudge = potluck.check(event, now)  # too many of the same kind of dish?
        if nudge:
            outbox.send_to_guest(guest["id"], nudge, channel=msg.channel,
                                 reply_ref=msg.reply_ref if msg.channel == "email" else None,
                                 kind="potluck")
    if was != "attending" and now["status"] == "attending":
        groups.share_link(event, now)  # just confirmed: send their group link


def process_email(event: dict) -> None:
    for msg in email.fetch_new(list(store.seen_ids("email"))):
        store.mark_seen("email", msg.reply_ref)
        guest = store.guest_by_email(event["id"], msg.sender_id)
        if not guest:
            # Not on the guest list: never answer (this also ignores newsletters,
            # welcome emails and anyone trying to talk Gus into things).
            store.activity("ignored", f"Ignored email from {msg.sender_id} (not on the guest list)")
            continue
        _reply(guest, msg, event)


def process_telegram(event: dict, wait_seconds: int) -> None:
    offset = int(store.get_kv("telegram_offset", "0"))
    updates, offset = telegram.fetch_updates(offset, wait_seconds=wait_seconds)
    store.set_kv("telegram_offset", str(offset))
    for u in updates:
        try:
            if "chat_join_request" in u:
                groups.handle_join_request(event, u["chat_join_request"])
            elif "my_chat_member" in u:
                m = u["my_chat_member"]
                if m["chat"].get("type") in ("group", "supergroup") and \
                        m.get("new_chat_member", {}).get("status") in ("member", "administrator"):
                    groups.handle_bot_added(event, m["chat"])
            elif "message" in u:
                msg = u["message"]
                if msg["chat"].get("type") in ("group", "supergroup"):
                    text = msg.get("text", "")
                    if text.startswith("/connect"):
                        parts = text.split(maxsplit=1)
                        groups.connect(event, msg["chat"], parts[1] if len(parts) > 1 else "")
                    elif text:
                        groups.handle_group_message(event, msg)
                elif msg.get("text"):
                    _private_telegram(event, telegram.as_incoming(msg))
        except Exception as exc:  # one bad update must not stop the rest
            store.activity("info", f"Telegram update failed: {exc}")


def _private_telegram(event: dict, msg: IncomingMessage) -> None:
    guest = store.guest_by_telegram(event["id"], msg.sender_id)
    if msg.text.startswith("/start"):
        parts = msg.text.split(maxsplit=1)
        code = parts[1] if len(parts) > 1 else ""
        linked = store.guest_by_code(event["id"], code) if code else None
        if linked:
            store.update_guest(linked["id"], telegram_chat_id=msg.sender_id,
                               preferred_channel="telegram",
                               status="invited" if linked["status"] == "new" else linked["status"],
                               invited_at=linked["invited_at"] or time.time())
            store.activity("info", f"{linked['name']} connected on Telegram")
            outbox.send_to_guest(
                linked["id"],
                f"Ah, {linked['name']}! Uncle Gus here, {event.get('host_name')}'s assistant "
                f"for {event.get('name')} on {event.get('date')}. Are you coming? Any dietary "
                "needs, and what could you bring? Ask me anything about the event too.",
                channel="telegram", kind="welcome")
            return
        if not guest:
            telegram.send(msg.sender_id, "Hello! I'm Uncle Gus. I only chat with invited "
                          "guests. Please use the invite link your host sent you.")
            store.activity("ignored", f"Telegram user {msg.sender_name} has no valid invite link")
            return
    if not guest:
        store.activity("ignored", f"Ignored Telegram message from {msg.sender_name} (not linked to a guest)")
        return
    _reply(guest, msg, event)


# ---------- follow-ups ----------
def run_followups(event: dict) -> None:
    wait = config.followup_after_minutes() * 60
    now = time.time()
    for guest in store.guests(event["id"]):
        if guest["status"] != "invited" or guest["last_inbound_at"]:
            continue  # only chase people who haven't said anything yet
        if not guest["email"] and not guest["telegram_chat_id"]:
            continue  # unreachable until they use their Telegram link
        last = guest["last_outbound_at"] or guest["invited_at"] or now
        if now - last < wait:
            continue
        if guest["followups_sent"] >= config.max_followups():
            if guest["followups_sent"] == config.max_followups():
                store.flag("no_reply", f"{guest['name']} hasn't replied after "
                           f"{guest['followups_sent']} follow-ups. Over to you.", guest["id"])
                store.update_guest(guest["id"], followups_sent=guest["followups_sent"] + 1)
            continue
        attempt = guest["followups_sent"] + 1
        text = llm.write_followup(uncle.facts_for(event, guest), guest["name"], attempt)
        outbox.send_to_guest(guest["id"], text, subject=f"Re: You're invited: {event.get('name')}",
                             kind="follow-up")
        # Count the attempt even if the outbox blocked it, so we never retry in a loop.
        store.update_guest(guest["id"], followups_sent=attempt, last_outbound_at=time.time())


# ---------- loop ----------
def tick(telegram_wait: int = 0) -> None:
    event = store.current_event()
    if not event:
        return
    for name, step in (("email", lambda: process_email(event)),
                       ("telegram", lambda: process_telegram(event, telegram_wait)),
                       ("follow-ups", lambda: run_followups(event))):
        try:
            step()
        except Exception as exc:  # one failing channel must not stop the others
            store.activity("info", f"{name} check failed: {exc}")
