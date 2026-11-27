"""Messaging channels. Each one turns its service into the same shape:

    IncomingMessage(channel, sender_id, sender_name, text, reply_ref)

so Uncle's logic doesn't care whether a guest wrote by email or Telegram.
Adding WhatsApp later means adding one more module here.
"""
from dataclasses import dataclass


@dataclass
class IncomingMessage:
    channel: str        # "email" or "telegram"
    sender_id: str      # email address or Telegram chat id
    sender_name: str
    text: str
    reply_ref: str      # what the channel needs to reply in-thread
