"""Email channel: Gather's own AgentMail inbox, via a Custom API connector.

AgentMail REST endpoints used (base https://api.agentmail.to):
  POST /v0/inboxes/{inbox}/messages/send
  GET  /v0/inboxes/{inbox}/messages
  GET  /v0/inboxes/{inbox}/messages/{message_id}
  POST /v0/inboxes/{inbox}/messages/{message_id}/reply
"""
import re

from .. import config, gateway
from . import IncomingMessage


def _inbox_path(suffix: str = "") -> str:
    return f"/v0/inboxes/{gateway.quote(config.inbox_id())}/messages{suffix}"


def send(to: str, subject: str, text: str) -> dict:
    """Send a new email. Returns {message_id, thread_id}."""
    return gateway.call("POST", config.agentmail_base(), _inbox_path("/send"),
                        body={"to": to, "subject": subject, "text": text})


def reply(message_id: str, text: str) -> dict:
    return gateway.call("POST", config.agentmail_base(),
                        _inbox_path(f"/{gateway.quote(message_id)}/reply"),
                        body={"text": text})


def _address(raw) -> str:
    """'Tola Ade <tola@example.com>' -> 'tola@example.com'"""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    match = re.search(r"<([^>]+)>", raw or "")
    return (match.group(1) if match else (raw or "")).strip().lower()


def _name(raw) -> str:
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    name = (raw or "").split("<")[0].strip().strip('"')
    return name or _address(raw).split("@")[0]


def get_full_text(message_id: str) -> str:
    msg = gateway.call("GET", config.agentmail_base(),
                       _inbox_path(f"/{gateway.quote(message_id)}"))
    # extracted_text strips quoted history from replies, which is what we want.
    return (msg.get("extracted_text") or msg.get("text")
            or msg.get("preview") or "").strip()


def fetch_new(seen_ids: list[str], limit: int = 25) -> list[IncomingMessage]:
    """Return messages from other people that we haven't handled yet."""
    data = gateway.call("GET", config.agentmail_base(), _inbox_path(),
                        params={"limit": limit})
    own = config.inbox_id().lower()
    new = []
    for item in data.get("messages", []):
        mid = item["message_id"]
        sender = _address(item.get("from"))
        if mid in seen_ids or sender == own:
            continue  # already handled, or a message Gather sent itself
        new.append(IncomingMessage(
            channel="email",
            sender_id=sender,
            sender_name=_name(item.get("from")),
            text=get_full_text(mid),
            reply_ref=mid,
        ))
    return list(reversed(new))  # oldest first
