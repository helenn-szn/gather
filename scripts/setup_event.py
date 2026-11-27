"""Create the event and guest list from a JSON file.

    python3 -m scripts.setup_event event.json

Prints each guest's private Telegram link. Guests without an email only
hear from Gus after they open their link.
"""
import json
import sys

from gather import agent, store


def baseline() -> None:
    """Mark everything already in the inbox and on Telegram as handled,
    so Gus doesn't answer old test messages when he starts."""
    from gather.channels import email, telegram
    try:
        old = email.fetch_new(list(store.seen_ids("email")), limit=100)
        for m in old:
            store.mark_seen("email", m.reply_ref)
        print(f"Skipping {len(old)} old email(s) already in the inbox.")
    except Exception as exc:
        print("Couldn't read the inbox to skip old emails:", exc)
    try:
        offset = int(store.get_kv("telegram_offset", "0"))
        msgs, offset = telegram.fetch_new(offset, wait_seconds=0)
        store.set_kv("telegram_offset", str(offset))
        print(f"Skipping {len(msgs)} old Telegram message(s).")
    except Exception as exc:
        print("Couldn't check Telegram for old messages:", exc)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    data = json.loads(open(sys.argv[1]).read())
    store.init()
    if store.current_event() and "--new" not in sys.argv:
        print("An event already exists. Re-run with --new to start a fresh one "
              "(the old one stays in the database).")
        return
    baseline()
    import secrets
    data["event"].setdefault("type", "dinner")
    # for /connect in a Telegram group; reuse the host's own if event.json has one (the gate needs it too)
    data["event"]["host_code"] = str(data["event"].get("host_code") or secrets.token_hex(3)).upper()
    event_id = store.create_event(data["event"])
    for g in data["guests"]:
        store.add_guest(event_id, g["name"], g.get("email"))
    store.activity("info", f"Event created: {data['event'].get('name')} "
                           f"with {len(data['guests'])} guests")

    print(f"Created '{data['event'].get('name')}' with {len(data['guests'])} guests.\n")
    for g in store.guests(event_id):
        link = agent.telegram_link(g) or "(Telegram not configured)"
        print(f"  {g['name']:<20} {g['email'] or '(no email)':<32} {link}")
    from gather import themes
    print(f"\nTheme: {themes.get(data['event'].get('type'))['label']}")
    print("\nTelegram group (optional): add the bot to your group as an admin with")
    print(f"'Invite users' permission, then send this in the group:  /connect {data['event']['host_code']}")
    print("\nNext: python3 -m scripts.invite")


if __name__ == "__main__":
    main()
