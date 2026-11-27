"""Settings for Gather, read from environment variables (or a .env file).

No API keys live here. Each service is reached through a Fly.io Sprites
connector, so the only values we need are the connectors' gateway base URLs.
Run `python -m scripts.check_connectors` on the Sprite to print them.
"""
import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so we don't need an extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(ROOT / ".env")


def _get(name: str, default: str | None = None, required: bool = True) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Missing setting {name}. Add it to .env (see .env.example)."
        )
    return value or ""


# Gateway base URLs, e.g. https://api.sprites.dev/v1/gateway/<provider>/<connection_id>
def agentmail_base() -> str:
    return _get("AGENTMAIL_GATEWAY").rstrip("/")


def telegram_base() -> str:
    return _get("TELEGRAM_GATEWAY").rstrip("/")


def openrouter_base() -> str:
    return _get("OPENROUTER_GATEWAY").rstrip("/")


# Path under the OpenRouter gateway that reaches the chat endpoint.
# Check the connector's usage_snippet; it is usually /chat/completions
# or /api/v1/chat/completions depending on the stored base URL.
def openrouter_chat_path() -> str:
    return _get("OPENROUTER_CHAT_PATH", "/chat/completions")


def model() -> str:
    return _get("GATHER_MODEL", "openai/gpt-4o-mini")


def inbox_id() -> str:
    """Gather's AgentMail inbox, e.g. uncle-gather@agentmail.to"""
    return _get("AGENTMAIL_INBOX")


def test_guest_email() -> str:
    return _get("TEST_GUEST_EMAIL")


def host_name() -> str:
    return _get("HOST_NAME", "your host", required=False)


def telegram_bot_username() -> str:
    """Optional. Looked up from Telegram (getMe) if not set."""
    return _get("TELEGRAM_BOT_USERNAME", "", required=False).lstrip("@")


# Follow-ups: how long to wait for a reply before chasing, and how many times.
# For the demo, set FOLLOWUP_AFTER_MINUTES=3 so you can watch it happen.
def followup_after_minutes() -> float:
    return float(_get("FOLLOWUP_AFTER_MINUTES", "1440", required=False))


def max_followups() -> int:
    return int(_get("MAX_FOLLOWUPS", "2", required=False))


# Outbox rules (Saturday these move to the separate policy-gate Sprite).
def max_messages_per_guest_per_hour() -> int:
    return int(_get("MAX_MESSAGES_PER_GUEST_PER_HOUR", "6", required=False))


def poll_seconds() -> int:
    return int(_get("POLL_SECONDS", "10", required=False))


def dashboard_key() -> str:
    """Secret needed to open the dashboard: https://<sprite-url>/?key=<this>"""
    return _get("DASHBOARD_KEY")


def dashboard_port() -> int:
    return int(_get("DASHBOARD_PORT", "8080", required=False))


# Pinecone memory (optional). Gateway URL of the Pinecone connector.
def pinecone_gateway() -> str:
    return _get("PINECONE_GATEWAY", "", required=False)


def pinecone_index() -> str:
    """Set when using the built-in Pinecone connector (records API by index name)."""
    return _get("PINECONE_INDEX", "", required=False)


def pinecone_namespace() -> str:
    return _get("PINECONE_NAMESPACE", "gather-guests", required=False)


def pinecone_text_field() -> str:
    """The index field that has integrated embedding."""
    return _get("PINECONE_TEXT_FIELD", "text", required=False)


# Policy gate. On the agent: AGENTMAIL_GATEWAY=gate:agentmail and
# TELEGRAM_GATEWAY=gate:telegram send everything through the gate at GATE_URL.
def gate_url() -> str:
    return _get("GATE_URL", "", required=False).rstrip("/")


def gate_secret() -> str:
    return _get("GATE_SECRET")


STATE_FILE = ROOT / "state.json"
