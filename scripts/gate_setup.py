"""On the GATE Sprite: give the gate the host's guest list and connect code.

    python3 -m scripts.gate_setup event.json HOSTCODE
    python3 -m scripts.gate_setup "ann@example.com,bo@example.com" HOSTCODE

Writes gate.json. Run it again whenever the guest list changes; the gate
picks up the new list without a restart. The agent can't change this file:
it lives on a different machine.
"""
import json
import sys

from gather import config
from gather.gate_server import GATE_FILE


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        return
    src = sys.argv[1]
    if src.endswith(".json"):
        data = json.loads(open(src, encoding="utf-8-sig").read())
        emails = sorted({g["email"].strip().lower() for g in data["guests"] if g.get("email")})
    else:
        emails = sorted({e.strip().lower() for e in src.split(",") if "@" in e})
    old = json.loads(GATE_FILE.read_text()) if GATE_FILE.exists() else {}
    gate = {"inbox": config.inbox_id(), "emails": emails, "host_code": sys.argv[2].strip().upper(),
            "groups": old.get("groups", [])}
    GATE_FILE.write_text(json.dumps(gate, indent=2))
    print(f"Gate allows email to {len(emails)} guest(s) from {gate['inbox']}:")
    for e in emails:
        print("  ", e)
    print(f"Telegram groups are allowed once someone sends /connect {gate['host_code']} in them.")


if __name__ == "__main__":
    main()
