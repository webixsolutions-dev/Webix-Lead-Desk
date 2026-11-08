"""Create a local configuration file once, keeping its random secret off stdout."""

import logging
import os
import secrets
from pathlib import Path


def main() -> None:
    """Write a restricted .env only when no existing configuration is present."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    contents = (root / ".env.example").read_text(encoding="utf-8")
    contents = contents.replace(
        "SESSION_SECRET=\n", f"SESSION_SECRET={secrets.token_urlsafe(48)}\n"
    )
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        logging.info("Existing .env preserved. No settings changed.")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(contents)
    (root / "var").mkdir(exist_ok=True)
    logging.info("Created local .env with a random session key. Keep it out of Git.")


if __name__ == "__main__":
    main()
