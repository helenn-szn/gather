"""Uncle's brain for tonight: read a guest message, understand it, reply.

Same logic for every channel. Friday adds the event store and Ask Uncle
answers from the event plan; Saturday puts the policy gate in front of
every outgoing message.
"""
import os

from . import llm
from .channels import IncomingMessage

EVENT = os.environ.get(
    "GATHER_EVENT",
    "Christmas dinner at the host's home in Lagos, Saturday 20 December, 6 p.m.",
)


def handle(msg: IncomingMessage) -> tuple[dict, str]:
    """Return (parsed reply, Uncle's response text)."""
    parsed = llm.parse_guest_reply(msg.sender_name, msg.text, EVENT)

    if parsed["suspicious"]:
        # Don't act on it. Reply neutrally and flag it for the host.
        response = (
            "Thank you for your message! I've noted your reply. "
            "Anything beyond your RSVP goes to the host to decide."
        )
        return parsed, response

    response = llm.uncle_says(
        f"A guest named {msg.sender_name} replied about this event: {EVENT}.\n"
        f"What we understood: {parsed['summary']}\n"
        f"Attending: {parsed['attending']}. Bringing: {parsed['bringing']}. "
        f"Dietary needs: {', '.join(parsed['dietary']) or 'none'}.\n"
        f"Questions they asked: {parsed['questions'] or 'none'}.\n"
        "Write a short, warm reply confirming what you understood. If they asked "
        "questions you can't answer from the event details, say you'll check with "
        "the host. Do not invent details. Do not use any markdown."
    )
    return parsed, response
