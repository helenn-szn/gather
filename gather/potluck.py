"""Duplicate-dish resolver: notice when too many people bring the same kind of thing.

Each contribution gets a category (drinks, dessert, main, side, snack, other).
When a category is already well covered, Uncle Gus gently suggests something
the table is missing, and makes clear the original offer is still welcome.
"""
from . import llm, store, themes

# How many contributions of each kind are "enough" before Gus suggests something else.
LIMITS = {"drinks": 2, "dessert": 2, "main": 2, "side": 3, "snack": 3}
SUGGEST_ORDER = ("dessert", "side", "drinks", "snack", "main")


def _category(guest: dict) -> str | None:
    if not guest.get("bringing"):
        return None
    if guest.get("bring_category"):
        return guest["bring_category"]
    cat = llm.categorize_item(guest["bringing"])
    store.update_guest(guest["id"], bring_category=cat)
    return cat


def check(event: dict, guest: dict) -> str | None:
    """Return a nudge for this guest if their item duplicates the table, else None."""
    guest = store.guest_by_id(guest["id"])
    cat = _category(guest)
    if not cat or cat not in LIMITS:
        return None
    others = [g for g in store.guests(event["id"])
              if g["id"] != guest["id"] and g["status"] != "declined" and g.get("bringing")]
    counts = {c: 0 for c in LIMITS}
    same = []
    for g in others:
        c = _category(g)
        if c in counts:
            counts[c] += 1
        if c == cat:
            same.append(f"{g['bringing']} ({g['name']})")
    if counts[cat] < LIMITS[cat]:
        return None
    missing = [c for c in SUGGEST_ORDER if c != cat and counts[c] < LIMITS[c]]
    if not missing:
        return None
    suggestion = min(missing, key=lambda c: counts[c])
    store.activity("potluck", f"{guest['name']}'s {guest['bringing']} makes {counts[cat] + 1} {cat}; "
                              f"suggested {suggestion}")
    return llm.write_duplicate_nudge(guest["name"], guest["bringing"], cat, same, suggestion,
                                     themes.get(event.get("type"))["tone"])
