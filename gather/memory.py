"""Uncle Gus's long-term memory, stored in Pinecone through a Sprites connector.

After each guest message Gus stores a short note ("At Christmas Dinner:
vegetarian, bringing puff-puff"). At the next event he looks up what he
remembers about that guest and uses it in invitations and replies.

Two ways to reach Pinecone, both with integrated embedding (Pinecone turns
text into vectors itself):
  * Records API through the built-in Pinecone connector (PINECONE_INDEX set):
      POST /indexes/{index}/records/namespaces/{ns}/upsert   (NDJSON)
      POST /indexes/{index}/records/namespaces/{ns}/search
  * Documents API straight to an index host (PINECONE_INDEX empty):
      POST /namespaces/{ns}/documents/upsert
      POST /namespaces/{ns}/documents/search   (score_by: embed)

Optional: if PINECONE_GATEWAY isn't set, memory is simply off. Any memory
failure is logged and ignored, so it can never stop Gus from replying.
"""
import time

import requests

from . import config, gateway

API_VERSION = "2026-07"          # documents API
RECORDS_API_VERSION = "2025-04"  # records API
TIMEOUT = 30


def enabled() -> bool:
    return bool(config.pinecone_gateway())


def guest_key(guest: dict) -> str | None:
    """A stable identity for the same person across events."""
    if guest.get("email"):
        return "email:" + guest["email"].lower()
    if guest.get("telegram_chat_id"):
        return "tg:" + str(guest["telegram_chat_id"])
    return None


def _records_mode() -> bool:
    return bool(config.pinecone_index())


def _post(path: str, body, ndjson: bool = False) -> dict:
    base = config.pinecone_gateway().rstrip("/")
    if _records_mode():
        base += f"/indexes/{config.pinecone_index()}"
    headers = {"X-Pinecone-Api-Version": RECORDS_API_VERSION if _records_mode() else API_VERSION}
    if ndjson:
        import json as _json
        headers["Content-Type"] = "application/x-ndjson"
        resp = requests.post(base + path, data="\n".join(_json.dumps(d) for d in body),
                             headers=headers, timeout=TIMEOUT)
    else:
        resp = requests.post(base + path, json=body, headers=headers, timeout=TIMEOUT)
    if resp.status_code >= 400:
        raise gateway.GatewayError(f"Pinecone {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def remember(event: dict, guest: dict, note: str, kind: str = "reply") -> None:
    """Store one short note about a guest."""
    key = guest_key(guest)
    if not enabled() or not key or not note.strip():
        return
    field = config.pinecone_text_field()
    doc = {
        "_id": f"e{event['id']}-g{guest['id']}-{int(time.time() * 1000)}",
        field: f"At {event.get('name', 'an event')} ({event.get('date', '')}): {note.strip()}",
        "guest_key": key,
        "guest_name": guest["name"],
        "event_id": str(event["id"]),
        "event_name": event.get("name", ""),
        "kind": kind,
        "created_at": int(time.time()),
    }
    ns = config.pinecone_namespace()
    try:
        if _records_mode():
            _post(f"/records/namespaces/{ns}/upsert", [doc], ndjson=True)
        else:
            _post(f"/namespaces/{ns}/documents/upsert", {"documents": [doc]})
    except Exception as exc:  # memory is a bonus, never a blocker
        from . import store
        store.activity("info", f"Couldn't save a memory for {guest['name']}: {exc}")


def recall(event: dict, guest: dict, about: str, k: int = 3) -> list[str]:
    """What Gus remembers about this guest from OTHER events, most relevant first."""
    key = guest_key(guest)
    if not enabled() or not key:
        return []
    field = config.pinecone_text_field()
    flt = {"$and": [{"guest_key": {"$eq": key}}, {"event_id": {"$ne": str(event["id"])}}]}
    ns = config.pinecone_namespace()
    try:
        if _records_mode():
            data = _post(f"/records/namespaces/{ns}/search", {
                "query": {"top_k": k, "inputs": {"text": about or "preferences"}, "filter": flt},
                "fields": [field]})
            hits = data.get("result", {}).get("hits", [])
            return [h.get("fields", {}).get(field, "") for h in hits if h.get("fields", {}).get(field)]
        data = _post(f"/namespaces/{ns}/documents/search", {
            "top_k": k,
            "score_by": [{"type": "embed", "fields": [field], "query": about or "preferences"}],
            "filter": flt, "include_fields": [field]})
    except Exception as exc:
        from . import store
        store.activity("info", f"Couldn't look up memories for {guest['name']}: {exc}")
        return []
    return [m.get(field, "") for m in data.get("matches", []) if m.get(field)]
