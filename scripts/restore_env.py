"""Rebuild the AGENT Sprite's .env if it got overwritten.

    python3 -m scripts.restore_env

Keeps AGENTMAIL_GATEWAY / TELEGRAM_GATEWAY / AGENTMAIL_INBOX from the current
.env, finds the OpenRouter and Pinecone gateways from the connector list,
makes a NEW dashboard key and a NEW gate secret, and saves the old file as
.env.broken. The gate secret is also written to ~/gate_secret.txt so it can be
copied to the gate Sprite without pasting.
"""
import hashlib
import re
import secrets
from pathlib import Path

from gather import gateway

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"


def _urls(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _urls(v, out)
    elif isinstance(obj, str):
        out.extend(re.findall(r"https://api\.sprites\.dev/v1/gateway/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+", obj))


def main() -> None:
    old = {}
    if ENV.exists():
        text = ENV.read_text(errors="replace")
        (ROOT / ".env.broken").write_text(text)
        for line in text.splitlines():
            m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
            if m:
                old[m.group(1)] = m.group(2).strip()

    urls = []
    try:
        _urls(gateway.list_connectors(), urls)
    except Exception as exc:
        print("Could not list connectors:", exc)
    urls = list(dict.fromkeys(urls))
    openrouter = next((u for u in urls if "/gateway/openrouter/" in u), "")
    pinecone = next((u for u in urls if "/gateway/pinecone/" in u), "")

    secret = secrets.token_hex(24)
    key = secrets.token_urlsafe(24)
    values = {
        "AGENTMAIL_GATEWAY": old.get("AGENTMAIL_GATEWAY", ""),
        "TELEGRAM_GATEWAY": old.get("TELEGRAM_GATEWAY", ""),
        "OPENROUTER_GATEWAY": openrouter,
        "OPENROUTER_CHAT_PATH": "/v1/chat/completions",
        "AGENTMAIL_INBOX": old.get("AGENTMAIL_INBOX", "uncle-gather@agentmail.to"),
        "TEST_GUEST_EMAIL": "tunmisealabi62@gmail.com",
        "HOST_NAME": "Helen",
        "GATHER_MODEL": "openai/gpt-4o-mini",
        "DASHBOARD_KEY": key,
        "DASHBOARD_PORT": "8080",
        "TELEGRAM_BOT_USERNAME": "unclegather_bot",
        "FOLLOWUP_AFTER_MINUTES": "1440",
        "MAX_FOLLOWUPS": "2",
        "MAX_MESSAGES_PER_GUEST_PER_HOUR": "6",
        "POLL_SECONDS": "10",
        "PINECONE_GATEWAY": pinecone,
        "PINECONE_INDEX": "gather-guests",
        "PINECONE_NAMESPACE": "gather-guests",
        "PINECONE_TEXT_FIELD": "chunk_text",
        "GATE_SECRET": secret,
    }
    ENV.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    Path.home().joinpath("gate_secret.txt").write_text(secret + "\n")

    print("Rebuilt", ENV)
    for k, v in values.items():
        shown = v if k not in ("DASHBOARD_KEY", "GATE_SECRET") else "(set)"
        print(f"  {k}={shown}")
    missing = [k for k in ("AGENTMAIL_GATEWAY", "TELEGRAM_GATEWAY", "OPENROUTER_GATEWAY",
                           "PINECONE_GATEWAY") if not values[k]]
    if missing:
        print("\nMISSING:", ", ".join(missing), "- tell Claude.")
    print("\nNew dashboard key (save it):", key)
    print("Gate secret fingerprint:", hashlib.sha256(secret.encode()).hexdigest()[:8])


if __name__ == "__main__":
    main()
