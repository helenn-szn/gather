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


STATE_FILE = ROOT / "state.json"
