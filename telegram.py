"""Telegram channel: a bot reached through a Custom API connector.

Telegram puts the bot token in the URL: https://api.telegram.org/bot<TOKEN>/<method>
Set up the connector so that TELEGRAM_GATEWAY + "/getMe" reaches that URL
(see README, step 4). Methods used: getMe, getUpdates, sendMessage.
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


def send(chat_id: int | str, text: str) -> dict:
    return _call("sendMessage", chat_id=chat_id, text=text)


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
