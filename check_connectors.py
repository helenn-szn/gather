"""Step 1 on the Sprite: list the connectors this Sprite can use.

    python3 -m scripts.check_connectors

Copy each connected provider's gateway_base_url into .env.
"""
from gather import gateway


def main() -> None:
    try:
        data = gateway.list_connectors()
    except Exception as exc:
        print("Could not reach the connector gateway.")
        print("Are you running this inside a Sprite? Error:", exc)
        return
    print(gateway.pretty(data))
    print("\nCopy each gateway_base_url into .env:")
    print("  AGENTMAIL_GATEWAY, TELEGRAM_GATEWAY, OPENROUTER_GATEWAY")
    print("Read OpenRouter's usage_snippet to confirm OPENROUTER_CHAT_PATH.")


if __name__ == "__main__":
    main()
