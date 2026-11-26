"""Messaging channels. Each one returns the same IncomingMessage shape."""
from dataclasses import dataclass


@dataclass
class IncomingMessage:
    channel: str        # "email" or "telegram"
    sender_id: str      # email address or Telegram chat id
    sender_name: str
    text: str
    reply_ref: str      # what the channel needs to reply in-thread
