"""Post (and pin) an update in the event's Telegram group.

    python3 -m scripts.announce "Dinner moves to 7 p.m. Bring a jumper!"
"""
import sys

from gather import groups, store


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    store.init()
    event = store.current_event()
    if not event or not groups.announce(event, " ".join(sys.argv[1:])):
        print("No Telegram group connected yet (or the post was blocked; see the dashboard).")
    else:
        print("Posted and pinned.")


if __name__ == "__main__":
    main()
