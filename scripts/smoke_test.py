"""Tonight's milestone: prove every piece works end to end.

Run on the Sprite, one step at a time:

    python3 -m scripts.smoke_test llm        # Uncle says hello (OpenRouter)
    python3 -m scripts.smoke_test invite     # send one invitation email
    python3 -m scripts.smoke_test email      # wait for the reply, parse it, answer it
    python3 -m scripts.smoke_test telegram   # message the bot, Uncle answers
    python3 -m scripts.smoke_test all        # everything above in order
"""
import sys
import time

from gather import config, gateway, state, uncle
from gather.channels import email, telegram

POLL_SECONDS = 15
WAIT_MINUTES = 15


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def test_llm() -> None:
    step("LLM through the OpenRouter connector")
    print(uncle.llm.uncle_says(
        "Introduce yourself in two sentences to a host who just hired you."))


def test_invite() -> None:
    step("Send one invitation email through AgentMail")
    body = uncle.llm.uncle_says(
        f"Write a short invitation email to a guest for: {uncle.EVENT}. "
        f"It's from {config.host_name()}'s assistant. Ask them to reply with "
        "whether they're coming, any dietary needs, and what they could bring. "
        "Address the guest as Tunmise. Sign off as: Uncle Gus, on behalf of " + config.host_name() + ". Never use placeholders in square brackets. Plain text only, no subject line, no markdown."
    )
    result = email.send(config.test_guest_email(), "You're invited! 🎄", body)
    print(f"Sent to {config.test_guest_email()}: {result}")
    print("Now reply to that email from the guest's inbox, then run: email")


def show(msg, parsed: dict, response: str) -> None:
    print(f"\nFrom {msg.sender_name} ({msg.channel}): {msg.text[:300]}")
    print("Understood:", gateway.pretty(parsed))
    if parsed["suspicious"]:
        print(f"!! FLAGGED FOR HOST: {parsed['suspicious_reason']}")
    print("Uncle replies:", response)


def test_email() -> None:
    step("Wait for an email reply, understand it, answer it")
    st = state.load()
    deadline = time.time() + WAIT_MINUTES * 60
    while time.time() < deadline:
        messages = email.fetch_new(st["seen_email_ids"])
        for msg in messages:
            parsed, response = uncle.handle(msg)
            show(msg, parsed, response)
            email.reply(msg.reply_ref, response)
            state.mark_email_seen(st, msg.reply_ref)
            state.save(st)
        if messages:
            return
        print(f"No new replies yet. Checking again in {POLL_SECONDS}s...")
        time.sleep(POLL_SECONDS)
    print("Gave up waiting. Reply to the invitation and run this again.")


def test_telegram() -> None:
    step("Telegram bot")
    bot = telegram.whoami()
    print(f"Bot is @{bot.get('username')}. Send it a message now, "
          "e.g. \"I'm coming! I'm vegetarian and can bring chin chin.\"")
    st = state.load()
    deadline = time.time() + WAIT_MINUTES * 60
    while time.time() < deadline:
        messages, st["telegram_offset"] = telegram.fetch_new(st["telegram_offset"])
        state.save(st)
        for msg in messages:
            if msg.text.startswith("/start"):
                telegram.send(msg.reply_ref, "Uncle here. Tell me: are you coming, "
                              "any dietary needs, and what can you bring?")
                continue
            parsed, response = uncle.handle(msg)
            show(msg, parsed, response)
            telegram.send(msg.reply_ref, response)
            st["telegram_chats"][msg.sender_id] = msg.sender_name
            state.save(st)
            return
    print("Gave up waiting for a Telegram message.")


STEPS = {"llm": test_llm, "invite": test_invite,
         "email": test_email, "telegram": test_telegram}


def main() -> None:
    choice = sys.argv[1] if len(sys.argv) > 1 else "all"
    if choice == "all":
        for fn in STEPS.values():
            fn()
    elif choice in STEPS:
        STEPS[choice]()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
