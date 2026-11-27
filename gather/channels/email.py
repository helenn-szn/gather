"""Email channel: Gather's own AgentMail inbox, reached through a connector.

AgentMail REST endpoints used (base https://api.agentmail.to):
  POST /v0/inboxes/{inbox}/messages/send
  GET  /v0/inboxes/{inbox}/messages
  GET  /v0/inboxes/{inbox}/messages/{message_id}         (optional)
  POST /v0/inboxes/{inbox}/messages/{message_id}/reply   (optional)

The built-in AgentMail connector only allows some endpoints. When reading a
single message or replying in-thread is blocked, we fall back to the list
preview and to a fresh "Re:" email, so nothing crashes. A Custom API
connector for AgentMail allows all endpoints and gives proper threading.
"""
import re

from .. import config, gateway
from . import IncomingMessage

# message_id -> (sender address, subject), so a fallback reply knows where to go
_SEEN_META: dict[str, tuple[str, str]] = {}


def _inbox_path(suffix: str = "") -> str:
    return f"/v0/inboxes/{gateway.quote(config.inbox_id())}/messages{suffix}"


def _blocked(exc: Exception) -> bool:
    if isinstance(exc, gateway.PolicyBlocked):
        return False  # a policy decision, not a missing endpoint: don't work around it
    text = str(exc)
    return "403" in text or "not supported" in text


def send(to: str, subject: str, text: str) -> dict:
    """Send a new email. Returns {message_id, thread_id}."""
    return gateway.call("POST", config.agentmail_base(), _inbox_path("/send"),
                        body={"to": to, "subject": subject, "text": text})


def reply(message_id: str, text: str) -> dict:
    """Reply in-thread; fall back to a new 'Re:' email if replying is blocked."""
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


def get_message(message_id: str) -> dict | None:
    """The full message, or None if the connector doesn't allow reading it."""
    try:
        return gateway.call("GET", config.agentmail_base(), _inbox_path(f"/{gateway.quote(message_id)}"))
    except gateway.GatewayError as exc:
        if _blocked(exc):
            return None
        raise


def get_full_text(message_id: str, preview: str = "", msg: dict | None = None) -> str:
    """Full body if the connector allows it, otherwise the list preview."""
    msg = msg if msg is not None else get_message(message_id)
    if msg is None:
        return preview.strip()
    # extracted_text strips quoted history from replies, which is what we want.
    return (msg.get("extracted_text") or msg.get("text") or preview or "").strip()


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
        _SEEN_META[mid] = (sender, item.get("subject") or "Your invitation")
        full = get_message(mid)
        new.append(IncomingMessage(
            channel="email",
            sender_id=sender,
            sender_name=_name(item.get("from")),
            text=get_full_text(mid, item.get("preview") or "", full),
            reply_ref=mid,
            attachments=[a for a in ((full or {}).get("attachments") or item.get("attachments") or [])
                         if isinstance(a, dict)],
        ))
    return list(reversed(new))  # oldest first
