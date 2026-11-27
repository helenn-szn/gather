"""Telegram channel: a bot reached through a Custom API connector.

Telegram puts the bot token in the URL: https://api.telegram.org/bot<TOKEN>/<method>
Set up the connector so that TELEGRAM_GATEWAY + "/getMe" reaches that URL
(see README). Methods used: getMe, getUpdates, sendMessage, createChatInviteLink,
approveChatJoinRequest, declineChatJoinRequest, pinChatMessage.
"""
from .. import config, gateway
from . import IncomingMessage


def _call(method: str, **payload) -> dict:
    data = gateway.call("POST", config.telegram_base(), f"/{method}",
                        body=payload or {})
    if not data.get("ok", False):
        raise gateway.GatewayError(f"Telegram {method} failed: {data}")
    return data["result"]


def whoami() -> dict:
    return _call("getMe")


def send(chat_id: int | str, text: str, reply_to: int | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    return _call("sendMessage", **payload)


def create_invite_link(chat_id: int | str, name: str) -> str:
    """A link that only *requests* to join; Gus approves or declines each request."""
    return _call("createChatInviteLink", chat_id=chat_id, name=name[:32],
                 creates_join_request=True)["invite_link"]


def approve_join(chat_id: int | str, user_id: int | str) -> None:
    _call("approveChatJoinRequest", chat_id=chat_id, user_id=user_id)


def decline_join(chat_id: int | str, user_id: int | str) -> None:
    _call("declineChatJoinRequest", chat_id=chat_id, user_id=user_id)


def pin(chat_id: int | str, message_id: int) -> None:
    _call("pinChatMessage", chat_id=chat_id, message_id=message_id, disable_notification=True)


def fetch_updates(offset: int, wait_seconds: int = 20) -> tuple[list[dict], int]:
    """Raw updates: private and group messages, join requests, bot membership."""
    updates = _call("getUpdates", offset=offset, timeout=wait_seconds,
                    allowed_updates=["message", "chat_join_request", "my_chat_member"])
    next_offset = offset
    for u in updates:
        next_offset = max(next_offset, u["update_id"] + 1)
    return updates, next_offset


def as_incoming(msg: dict) -> IncomingMessage:
    sender = msg.get("from", {})
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))
    return IncomingMessage(channel="telegram", sender_id=str(msg["chat"]["id"]),
                           sender_name=name or sender.get("username", "guest"),
                           text=msg.get("text", ""), reply_ref=str(msg["chat"]["id"]))


def fetch_new(offset: int, wait_seconds: int = 20) -> tuple[list[IncomingMessage], int]:
    """Long-poll for new messages. Returns (messages, next_offset)."""
    updates = _call("getUpdates", offset=offset, timeout=wait_seconds,
                    allowed_updates=["message"])
    messages, next_offset = [], offset
    for update in updates:
        next_offset = max(next_offset, update["update_id"] + 1)
        msg = update.get("message") or {}
        text = msg.get("text")
        if not text:
            continue  # stickers, photos etc. are ignored for now
        sender = msg.get("from", {})
        name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))
        chat_id = msg["chat"]["id"]
        messages.append(IncomingMessage(
            channel="telegram",
            sender_id=str(chat_id),
            sender_name=name or sender.get("username", "guest"),
            text=text,
            reply_ref=str(chat_id),
        ))
    return messages, next_offset
