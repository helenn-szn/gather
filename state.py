"""Small JSON file for remembering what Gather has already handled.

The Sprite's disk persists between runs, so this survives restarts.
It is replaced by a proper event store (SQLite) on Friday.
"""
import json

from .config import STATE_FILE


def load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen_email_ids": [], "telegram_offset": 0, "telegram_chats": {}}


def save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def mark_email_seen(state: dict, message_id: str) -> None:
    if message_id not in state["seen_email_ids"]:
        state["seen_email_ids"].append(message_id)
