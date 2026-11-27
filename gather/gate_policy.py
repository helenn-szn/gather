"""The policy gate's rules: what the agent may do with email and Telegram.

The gate Sprite is the only machine with access to the AgentMail and Telegram
connectors. The agent sends every request through the gate; each one is
checked here first. These rules live on a different machine from the AI, so
a confused or manipulated agent can't talk its way past them.

Rules
  * Reads (new email, Telegram updates) pass through.
  * Email may only go to addresses on the host's guest list (gate.json),
    only from Gather's own inbox, with no extra recipients (cc/bcc).
  * Email replies only go to guests: the gate looks up the original message
    itself instead of trusting the agent.
  * Telegram private messages: Telegram only lets bots message people who
    messaged the bot first, so strangers are already impossible.
  * Telegram group actions only in groups the host connected with the
    host code (the gate checks the /connect messages itself).
  * Rate limits per recipient and overall; message length cap; changing the
    inbox or anything else is refused.
"""
import json
import re
import time
from collections import defaultdict, deque
from urllib.parse import unquote

MAX_TEXT = 2500
PER_RECIPIENT_PER_HOUR = 6
GROUP_PER_HOUR = 15
TOTAL_PER_HOUR = 80


class Decision:
    def __init__(self, allowed: bool, reason: str, kind: str = "read"):
        self.allowed, self.reason, self.kind = allowed, reason, kind


class Policy:
    def __init__(self, config: dict, fetch_message=None):
        """config: {"inbox": ..., "emails": [...], "host_code": ...}
        fetch_message(message_id) -> dict, used to check who a reply goes to."""
        self.inbox = config["inbox"].lower()
        self.emails = {e.strip().lower() for e in config.get("emails", []) if e}
        self.host_code = str(config.get("host_code", "")).upper()
        self.groups = set(config.get("groups", []))
        self.fetch_message = fetch_message
        self.sent = defaultdict(deque)  # recipient -> send times

    # ---------- rate limiting ----------
    def _rate_ok(self, key: str, limit: int) -> bool:
        now = time.time()
        for k in (key, "*"):
            q = self.sent[k]
            while q and now - q[0] > 3600:
                q.popleft()
        return len(self.sent[key]) < limit and len(self.sent["*"]) < TOTAL_PER_HOUR

    def record(self, key: str) -> None:
        now = time.time()
        self.sent[key].append(now)
        self.sent["*"].append(now)

    @staticmethod
    def _addr(raw) -> str:
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        m = re.search(r"<([^>]+)>", raw or "")
        return (m.group(1) if m else (raw or "")).strip().lower()

    # ---------- watching Telegram updates for the host's /connect ----------
    def observe_updates(self, result: dict) -> None:
        for u in (result or {}).get("result", []):
            msg = u.get("message") or {}
            text = msg.get("text", "")
            if msg.get("chat", {}).get("type") in ("group", "supergroup") and text.startswith("/connect"):
                parts = text.split(maxsplit=1)
                if self.host_code and len(parts) > 1 and parts[1].strip().upper() == self.host_code:
                    self.groups.add(str(msg["chat"]["id"]))

    # ---------- the decision ----------
    def check(self, service: str, method: str, path: str, params: dict | None, body) -> Decision:
        path = "/" + path.lstrip("/")
        body = body or {}
        if len(json.dumps(body)) > 20000:
            return Decision(False, "request too large", "write")
        if service == "agentmail":
            return self._agentmail(method, path, params or {}, body)
        if service == "telegram":
            return self._telegram(path, body)
        return Decision(False, f"unknown service {service}", "write")

    def _agentmail(self, method: str, path: str, params: dict, body: dict) -> Decision:
        m = re.fullmatch(r"/v0/inboxes/([^/]+)/messages(?:/(send)|/([^/]+?)(/reply)?)?", path)
        if not m or unquote(m.group(1)).lower() != self.inbox:
            return Decision(False, "only Gather's own inbox messages are allowed", "write")
        is_send, msg_id, is_reply = m.group(2), m.group(3), m.group(4)
        if method == "GET" and not is_reply and not is_send:
            return Decision(True, "read", "read")
        if method != "POST":
            return Decision(False, f"{method} not allowed", "write")
        if is_send:
            extra = set(body) - {"to", "subject", "text"}
            if extra:
                return Decision(False, f"fields not allowed: {', '.join(sorted(extra))}", "write")
            to = body.get("to")
            recipients = to if isinstance(to, list) else [to]
            if len(recipients) != 1:
                return Decision(False, "exactly one recipient per email", "write")
            addr = self._addr(recipients[0])
            if addr not in self.emails:
                return Decision(False, f"{addr or 'recipient'} is not on the host's guest list", "write")
            return self._text_and_rate(body.get("text", ""), "email:" + addr)
        if is_reply and msg_id:
            if set(body) - {"text"}:
                return Decision(False, "replies may only contain text", "write")
            if not self.fetch_message:
                return Decision(False, "can't verify reply recipient", "write")
            try:
                original = self.fetch_message(unquote(msg_id))
            except Exception as exc:
                return Decision(False, f"couldn't look up the message being replied to: {exc}", "write")
            addr = self._addr(original.get("from"))
            if addr not in self.emails:
                return Decision(False, f"reply would go to {addr}, who is not on the guest list", "write")
            return self._text_and_rate(body.get("text", ""), "email:" + addr)
        return Decision(False, "email action not allowed", "write")

    def _telegram(self, path: str, body: dict) -> Decision:
        method = path.strip("/")
        if method in ("getMe", "getUpdates"):
            return Decision(True, "read", "read")
        chat = str(body.get("chat_id", ""))
        is_group = chat.startswith("-")
        if method == "sendMessage":
            if set(body) - {"chat_id", "text", "reply_parameters"}:
                return Decision(False, "only plain text messages", "write")
            if is_group and chat not in self.groups:
                return Decision(False, "group not connected by the host", "write")
            return self._text_and_rate(body.get("text", ""), "tg:" + chat,
                                       GROUP_PER_HOUR if is_group else PER_RECIPIENT_PER_HOUR)
        if method in ("createChatInviteLink", "approveChatJoinRequest", "declineChatJoinRequest",
                      "pinChatMessage"):
            if chat not in self.groups:
                return Decision(False, "group not connected by the host", "write")
            if method == "createChatInviteLink" and not body.get("creates_join_request"):
                return Decision(False, "invite links must require approval", "write")
            return Decision(True, method, "write")
        return Decision(False, f"Telegram {method} not allowed", "write")

    def _text_and_rate(self, text: str, key: str, limit: int = PER_RECIPIENT_PER_HOUR) -> Decision:
        if not text or not text.strip():
            return Decision(False, "empty message", "write")
        if len(text) > MAX_TEXT:
            return Decision(False, f"message too long ({len(text)} characters)", "write")
        if not self._rate_ok(key, limit):
            return Decision(False, f"rate limit reached for {key.split(':', 1)[1]}", "write")
        return Decision(True, "allowed", "write:" + key)
