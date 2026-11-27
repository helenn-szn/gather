"""Gather's memory: one SQLite file on the Sprite's persistent disk.

Tables
  event      the gathering (one active event for now)
  guests     who's invited, how to reach them, and what they told us
  messages   every message in and out, per guest
  activity   the feed shown on the host dashboard
  flags      things that need the host's attention
  seen       external message ids already handled (email ids, Telegram updates)
  kv         small settings, e.g. the Telegram update offset
"""
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import ROOT

DB_FILE = ROOT / "gather.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY,
    data TEXT NOT NULL,              -- JSON: name, date, time, location, details...
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS guests (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    telegram_chat_id TEXT,
    invite_code TEXT UNIQUE NOT NULL,
    preferred_channel TEXT NOT NULL DEFAULT 'email',
    status TEXT NOT NULL DEFAULT 'new',   -- new, invited, attending, declined, maybe
    party_size INTEGER NOT NULL DEFAULT 1,
    dietary TEXT NOT NULL DEFAULT '[]',
    bringing TEXT,
    notes TEXT,
    invited_at REAL,
    last_inbound_at REAL,
    last_outbound_at REAL,
    followups_sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    guest_id INTEGER,
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,        -- in / out
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,             -- invite, reply, answer, followup, ignored, blocked, flag, info
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY,
    guest_id INTEGER,
    kind TEXT NOT NULL,             -- suspicious, question, no_reply, blocked, unknown_sender
    text TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    channel TEXT NOT NULL,
    external_id TEXT NOT NULL,
    PRIMARY KEY (channel, external_id)
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


@contextmanager
def db():
    with _lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


GUEST_COLUMNS = {  # added after the first release; created if missing
    "telegram_user_id": "TEXT",
    "group_link": "TEXT",
    "group_link_sent": "INTEGER NOT NULL DEFAULT 0",
    "group_joined": "INTEGER NOT NULL DEFAULT 0",
    "bring_category": "TEXT",
    "wishlist": "TEXT",
}


def init() -> None:
    with db() as c:
        c.executescript(SCHEMA)
        have = {r[1] for r in c.execute("PRAGMA table_info(guests)").fetchall()}
        for col, decl in GUEST_COLUMNS.items():
            if col not in have:
                c.execute(f"ALTER TABLE guests ADD COLUMN {col} {decl}")


# ---------- event ----------
def create_event(data: dict) -> int:
    with db() as c:
        cur = c.execute("INSERT INTO event (data, created_at) VALUES (?, ?)",
                        (json.dumps(data), time.time()))
        return cur.lastrowid


def _event(row) -> dict | None:
    if not row:
        return None
    return {"id": row["id"], **json.loads(row["data"]), "created_at": row["created_at"]}


def event_by_id(event_id: int) -> dict | None:
    with db() as c:
        return _event(c.execute("SELECT * FROM event WHERE id=?", (event_id,)).fetchone())


def list_events() -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM event ORDER BY id DESC").fetchall()
    return [_event(r) for r in rows]


def current_event() -> dict | None:
    """The event Uncle Gus is working on: the one the host launched last
    (or, for older databases, the newest event)."""
    active = get_kv("active_event")
    if active:
        ev = event_by_id(int(active))
        if ev:
            return ev
    with db() as c:
        return _event(c.execute("SELECT * FROM event ORDER BY id DESC LIMIT 1").fetchone())


def set_active_event(event_id: int) -> None:
    set_kv("active_event", str(event_id))


def update_event(event_id: int, **fields) -> None:
    with db() as c:
        row = c.execute("SELECT data FROM event WHERE id=?", (event_id,)).fetchone()
        data = {**json.loads(row["data"]), **fields}
        c.execute("UPDATE event SET data=? WHERE id=?", (json.dumps(data), event_id))


def guest_by_telegram_user(event_id: int, user_id: str) -> dict | None:
    with db() as c:
        return _guest(c.execute(
            "SELECT * FROM guests WHERE event_id=? AND (telegram_user_id=? OR telegram_chat_id=?)",
            (event_id, str(user_id), str(user_id))).fetchone())


# ---------- guests ----------
def _guest(row) -> dict | None:
    if not row:
        return None
    g = dict(row)
    g["dietary"] = json.loads(g["dietary"] or "[]")
    return g


def add_guest(event_id: int, name: str, email: str | None = None) -> int:
    code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
    with db() as c:
        cur = c.execute(
            "INSERT INTO guests (event_id, name, email, invite_code) VALUES (?, ?, ?, ?)",
            (event_id, name, (email or "").strip().lower() or None, code))
        return cur.lastrowid


def remove_guest(guest_id: int) -> None:
    with db() as c:
        c.execute("DELETE FROM guests WHERE id=?", (guest_id,))


def guests(event_id: int) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM guests WHERE event_id=? ORDER BY id",
                         (event_id,)).fetchall()
    return [_guest(r) for r in rows]


def guest_by_id(guest_id: int) -> dict | None:
    with db() as c:
        return _guest(c.execute("SELECT * FROM guests WHERE id=?", (guest_id,)).fetchone())


def guest_by_email(event_id: int, email: str) -> dict | None:
    with db() as c:
        return _guest(c.execute(
            "SELECT * FROM guests WHERE event_id=? AND email=?",
            (event_id, email.strip().lower())).fetchone())


def guest_by_telegram(event_id: int, chat_id: str) -> dict | None:
    with db() as c:
        return _guest(c.execute(
            "SELECT * FROM guests WHERE event_id=? AND telegram_chat_id=?",
            (event_id, str(chat_id))).fetchone())


def guest_by_code(event_id: int, code: str) -> dict | None:
    with db() as c:
        return _guest(c.execute(
            "SELECT * FROM guests WHERE event_id=? AND invite_code=?",
            (event_id, code.strip())).fetchone())


def update_guest(guest_id: int, **fields) -> None:
    if not fields:
        return
    if "dietary" in fields:
        fields["dietary"] = json.dumps(fields["dietary"])
    cols = ", ".join(f"{k}=?" for k in fields)
    with db() as c:
        c.execute(f"UPDATE guests SET {cols} WHERE id=?", (*fields.values(), guest_id))


# ---------- messages, activity, flags ----------
def log_message(guest_id, channel: str, direction: str, text: str) -> None:
    with db() as c:
        c.execute("INSERT INTO messages (guest_id, channel, direction, text, created_at) "
                  "VALUES (?, ?, ?, ?, ?)", (guest_id, channel, direction, text, time.time()))


def recent_outbound_count(guest_id: int, seconds: int) -> int:
    with db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM messages WHERE guest_id=? AND direction='out' AND created_at>?",
            (guest_id, time.time() - seconds)).fetchone()[0]


def recent_group_outbound(seconds: int) -> int:
    with db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM messages WHERE channel='group' AND direction='out' AND created_at>?",
            (time.time() - seconds,)).fetchone()[0]


def conversation(guest_id: int, limit: int = 8) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT direction, text FROM messages WHERE guest_id=? "
                         "ORDER BY id DESC LIMIT ?", (guest_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def activity(kind: str, text: str) -> None:
    print(f"[{kind}] {text}", flush=True)
    with db() as c:
        c.execute("INSERT INTO activity (kind, text, created_at) VALUES (?, ?, ?)",
                  (kind, text, time.time()))


def flag(kind: str, text: str, guest_id: int | None = None) -> None:
    """Raise something for the host. Identical open flags aren't repeated."""
    with db() as c:
        if c.execute("SELECT 1 FROM flags WHERE resolved=0 AND kind=? AND text=?",
                     (kind, text)).fetchone():
            return
        c.execute("INSERT INTO flags (guest_id, kind, text, created_at) VALUES (?, ?, ?, ?)",
                  (guest_id, kind, text, time.time()))
    activity("flag", text)


def recent_activity(limit: int = 40) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT kind, text, created_at FROM activity ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]


def open_flags() -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT f.id, f.kind, f.text, f.created_at, g.name AS guest "
                         "FROM flags f LEFT JOIN guests g ON g.id=f.guest_id "
                         "WHERE f.resolved=0 ORDER BY f.id DESC").fetchall()
    return [dict(r) for r in rows]


def resolve_flag(flag_id: int) -> None:
    with db() as c:
        c.execute("UPDATE flags SET resolved=1 WHERE id=?", (flag_id,))


# ---------- seen ids and small settings ----------
def seen_ids(channel: str) -> set[str]:
    with db() as c:
        rows = c.execute("SELECT external_id FROM seen WHERE channel=?", (channel,)).fetchall()
    return {r[0] for r in rows}


def mark_seen(channel: str, external_id: str) -> None:
    with db() as c:
        c.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (channel, external_id))


def get_kv(key: str, default: str | None = None) -> str | None:
    with db() as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_kv(key: str, value: str) -> None:
    with db() as c:
        c.execute("INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, value))
