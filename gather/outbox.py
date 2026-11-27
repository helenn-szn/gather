"""Every message Uncle Gus sends goes through here.

Rules checked before anything leaves:
  1. The recipient must be a guest of the current event. Gus can't message
     anyone else, whatever a message or the model says.
  2. Rate limit per guest, so a loop or a manipulated model can't spam.
  3. Message length cap.
Blocked sends are not sent; they're flagged for the host and shown on the
dashboard.

Saturday this check moves to a separate policy-gate Sprite that holds the
only send access, so even a compromised agent process can't skip it.
"""
import time

from . import config, gateway, store
from .channels import email, telegram

MAX_LENGTH = 2500


def _block(guest_id, reason: str) -> bool:
    store.flag("blocked", f"Blocked a message: {reason}", guest_id)
    return False


GROUP_MAX_PER_HOUR = 12


def send_to_group(event: dict, text: str, reply_to: int | None = None, pin_it: bool = False) -> bool:
    """Post in the event's connected Telegram group, with its own rate limit."""
    import json
    raw = store.get_kv(f"group:{event['id']}")
    if not raw:
        return _block(None, "no Telegram group connected")
    info = json.loads(raw)
    if store.recent_group_outbound(3600) >= GROUP_MAX_PER_HOUR:
        return _block(None, f"more than {GROUP_MAX_PER_HOUR} group messages in an hour")
    text = text.strip()
    if not text or len(text) > MAX_LENGTH:
        return _block(None, "group message empty or too long")
    try:
        sent = telegram.send(info["chat_id"], text, reply_to=reply_to)
    except gateway.PolicyBlocked as exc:
        return _block(None, f"the policy gate refused a group message ({exc})")
    if pin_it and sent.get("message_id"):
        try:
            telegram.pin(info["chat_id"], sent["message_id"])
        except Exception as exc:
            store.activity("info", f"Couldn't pin the update: {exc}")
    store.log_message(None, "group", "out", text)
    return True


def send_to_guest(guest_id: int, text: str, *, subject: str = "A note from Uncle Gus",
                  reply_ref: str | None = None, channel: str | None = None,
                  kind: str = "message") -> bool:
    event = store.current_event()
    guest = store.guest_by_id(guest_id)

    # Rule 1: only real guests of the current event.
    if not event or not guest or guest["event_id"] != event["id"]:
        return _block(guest_id, "recipient is not on the guest list")

    # Rule 2: rate limit.
    limit = config.max_messages_per_guest_per_hour()
    if store.recent_outbound_count(guest_id, 3600) >= limit:
        return _block(guest_id, f"more than {limit} messages to {guest['name']} in an hour")

    # Rule 3: length.
    text = text.strip()
    if not text:
        return _block(guest_id, "empty message")
    if len(text) > MAX_LENGTH:
        return _block(guest_id, f"message to {guest['name']} too long ({len(text)} chars)")

    channel = channel or guest["preferred_channel"]
    if channel == "telegram" and not guest["telegram_chat_id"]:
        channel = "email"
    if channel == "email" and not guest["email"]:
        return _block(guest_id, f"no way to reach {guest['name']} yet (no email, Telegram not linked)")

    try:
        if channel == "telegram":
            telegram.send(guest["telegram_chat_id"], text)
        elif reply_ref:
            email.reply(reply_ref, text)
        else:
            email.send(guest["email"], subject, text)
    except gateway.PolicyBlocked as exc:
        return _block(guest_id, f"the policy gate refused it ({exc})")

    store.log_message(guest_id, channel, "out", text)
    store.update_guest(guest_id, last_outbound_at=time.time())
    store.activity(kind, f"Sent {kind} to {guest['name']} by {channel}")
    return True
