"""Email channel: Gather's own AgentMail inbox, reached through a connector.
Falls back to the list preview and a fresh 'Re:' email when the connector
blocks reading single messages or replying in-thread."""
import re

from .. import config, gateway
from . import IncomingMessage

_SEEN_META: dict[str, tuple[str, str]] = {}


def _inbox_path(suffix: str = "") -> str:
    return f"/v0/inboxes/{gateway.quote(config.inbox_id())}/messages{suffix}"


def _blocked(exc: Exception) -> bool:
    text = str(exc)
    return "403" in text or "not supported" in text


def send(to: str, subject: str, text: str) -> dict:
    return gateway.call("POST", config.agentmail_base(), _inbox_path("/send"),
                        body={"to": to, "subject": subject, "text": text})


def reply(message_id: str, text: str) -> dict:
    try:
        return gateway.call("POST", config.agentmail_base(),
                            _inbox_path(f"/{gateway.quote(message_id)}/reply"),
                            body={"text": text})
    except gateway.GatewayError as exc:
        if not _blocked(exc) or message_id not in _SEEN_META:
            raise
        to, subject = _SEEN_META[message_id]
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        print("(reply endpoint blocked by connector; sending a new 'Re:' email instead)")
        return send(to, subject, text)


def _address(raw) -> str:
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    match = re.search(r"<([^>]+)>", raw or "")
    return (match.group(1) if match else (raw or "")).strip().lower()


def _name(raw) -> str:
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    name = (raw or "").split("<")[0].strip().strip('"')
    return name or _address(raw).split("@")[0]


def get_full_text(message_id: str, preview: str = "") -> str:
    try:
        msg = gateway.call("GET", config.agentmail_base(),
                           _inbox_path(f"/{gateway.quote(message_id)}"))
    except gateway.GatewayError as exc:
        if _blocked(exc):
            return preview.strip()
        raise
    return (msg.get("extracted_text") or msg.get("text") or preview or "").strip()


def fetch_new(seen_ids: list[str], limit: int = 25) -> list[IncomingMessage]:
    data = gateway.call("GET", config.agentmail_base(), _inbox_path(),
                        params={"limit": limit})
    own = config.inbox_id().lower()
    new = []
    for item in data.get("messages", []):
        mid = item["message_id"]
        sender = _address(item.get("from"))
        if mid in seen_ids or sender == own:
            continue
        _SEEN_META[mid] = (sender, item.get("subject") or "Your invitation")
        new.append(IncomingMessage(
            channel="email", sender_id=sender, sender_name=_name(item.get("from")),
            text=get_full_text(mid, item.get("preview") or ""), reply_ref=mid,
        ))
    return list(reversed(new))
