"""Uncle Gus's brain: understand a guest message, update the guest list, reply.

Same logic for every channel. Guest text is always treated as untrusted data.
"""
import json
import os
import time

from . import llm, memory, store, themes
from .channels import IncomingMessage

# Used only by the Thursday smoke test, which has no event store.
EVENT = os.environ.get(
    "GATHER_EVENT",
    "Christmas dinner at the host's home, Saturday 20 December, 6 p.m.",
)

STATUS_FROM_ATTENDING = {"yes": "attending", "no": "declined", "maybe": "maybe"}


def facts_for(event: dict, guest: dict | None) -> dict:
    """What Gus may tell this guest. Private details only go to confirmed guests."""
    private = ("id", "confirmed_only", "budget", "host_code", "type", "created_at", "status",
               "date_iso", "rsvp_iso", "launched_at", "type_label", "timezone", "food")
    facts = {k: v for k, v in event.items() if k not in private}
    theme = themes.get(event.get("type"))
    facts["event_type"] = themes.label_for(event)
    facts["style"] = theme["tone"]
    food = event.get("food", "home")
    if food == "out":
        facts["food"] = "We're eating out. Guests don't need to bring food; they just order and enjoy."
    elif food == "catered":
        facts["food"] = "The food is catered. Guests don't need to bring food."
    if guest and guest.get("status") == "attending":
        facts.update(event.get("confirmed_only", {}))
    else:
        facts["note"] = "Some details (like the exact address) are shared after the guest confirms."
    if guest:
        from . import santa
        ss = santa.facts_for_guest(event, guest)
        if ss:
            facts["secret_santa"] = ss
    return facts


def handle(msg: IncomingMessage) -> tuple[dict, str]:
    """Thursday smoke-test version: no event store, just parse and reply."""
    parsed = llm.parse_guest_reply(msg.sender_name, msg.text, EVENT)
    if parsed["suspicious"]:
        return parsed, ("Thank you for your message! I've noted your reply. "
                        "Anything beyond your RSVP goes to the host to decide.")
    response = llm.uncle_says(
        f"A guest named {msg.sender_name} replied about this event: {EVENT}.\n"
        f"What we understood: {parsed['summary']}\n"
        f"Questions they asked: {parsed['questions'] or 'none'}.\n"
        "Write a short, warm reply confirming what you understood. Plain text, no markdown.")
    return parsed, response


def apply_updates(guest: dict, parsed: dict, source: str = "") -> dict:
    """Update the guest list from what a guest told us. Returns the changes."""
    updates = {}
    status = STATUS_FROM_ATTENDING.get(parsed["attending"])
    if status:
        updates["status"] = status
    try:
        size = int(parsed.get("party_size") or 0)
    except (TypeError, ValueError):
        size = 0
    if 0 < size <= 20:  # ignore nonsense numbers
        updates["party_size"] = size
    if parsed.get("dietary"):
        updates["dietary"] = sorted(set(guest["dietary"]) | set(parsed["dietary"]))
    if parsed.get("wishlist") and str(parsed["wishlist"]).strip() != (guest.get("wishlist") or ""):
        updates["wishlist"] = str(parsed["wishlist"]).strip()[:300]
    if parsed.get("bringing"):
        updates["bringing"] = parsed["bringing"]
        updates["bring_category"] = None  # recategorised on the next duplicate check
    store.update_guest(guest["id"], **updates)
    if updates:
        store.activity("reply", f"{guest['name']}{source}: {parsed.get('summary') or 'updated'}")
    return updates


def handle_guest_message(event: dict, guest: dict, msg: IncomingMessage) -> str:
    """Update the guest from their message and return Gus's reply text."""
    store.log_message(guest["id"], msg.channel, "in", msg.text)
    store.update_guest(guest["id"], last_inbound_at=time.time())

    parsed = llm.parse_guest_reply(guest["name"], msg.text,
                                   json.dumps(facts_for(event, guest)))

    if parsed["suspicious"]:
        # Don't change anything based on a message that tries to instruct Gus.
        store.flag("suspicious",
                   f"{guest['name']}'s message tried to give Gus instructions "
                   f"({parsed.get('suspicious_reason') or 'unclear'}). Not acted on.",
                   guest["id"])
        return ("Thank you! I've noted your reply. Anything beyond your RSVP goes "
                f"to {event.get('host_name', 'the host')} to decide.\n\nUncle Gus")

    changes = apply_updates(guest, parsed)

    guest = store.guest_by_id(guest["id"])  # fresh, so confirmed details unlock
    if changes.get("wishlist"):
        from . import santa
        santa.wishlist_updated(event, guest)

    # Long-term memory (Pinecone): save what we learned, recall past events.
    note_parts = [parsed.get("summary") or ""]
    if parsed.get("dietary"):
        note_parts.append("Dietary: " + ", ".join(parsed["dietary"]) + ".")
    if parsed.get("bringing"):
        note_parts.append(f"Offered to bring {parsed['bringing']}.")
    memory.remember(event, guest, " ".join(p for p in note_parts if p))
    facts = facts_for(event, guest)
    remembered = memory.recall(event, guest, msg.text)
    if remembered:
        facts["uncle_remembers_from_past_events"] = remembered
        store.activity("memory", f"Remembered {len(remembered)} thing(s) about {guest['name']} from past events")

    out = llm.compose_reply(facts, guest["name"], parsed,
                            msg.text, store.conversation(guest["id"]))
    for question in out["for_host"]:
        store.flag("question", f"{guest['name']} asks: {question}", guest["id"])
    if parsed.get("questions"):
        store.activity("answer", f"Answered {guest['name']}'s question(s) via Ask Uncle")
    return out["reply"] or "Noted, thank you!\n\nUncle Gus"
