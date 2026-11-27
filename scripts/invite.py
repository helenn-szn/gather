"""Send invitations to every guest who hasn't been invited yet.

    python3 -m scripts.invite
"""
from gather import agent, store


def main() -> None:
    store.init()
    agent.invite_all()


if __name__ == "__main__":
    main()
