"""Run Uncle Gus: the live dashboard plus the listen/answer/chase loop.

Test it in the console:
    python3 -m scripts.run_agent

Run it for real as a Sprite service (keeps running, restarts on boot,
and the Sprite's URL points at the dashboard):
    sprite-env services create gather --cmd python3 \\
        --args "-m,scripts.run_agent" --dir /home/sprite/gather --http-port 8080
"""
import threading
import time

from gather import agent, config, dashboard, store


def main() -> None:
    store.init()
    threading.Thread(target=dashboard.serve, daemon=True).start()
    store.activity("info", "Uncle Gus is on duty")
    while True:
        started = time.time()
        # Telegram long-polling doubles as the wait between checks.
        agent.tick(telegram_wait=config.poll_seconds())
        if time.time() - started < 2:  # e.g. Telegram not reachable: don't spin
            time.sleep(config.poll_seconds())


if __name__ == "__main__":
    main()
