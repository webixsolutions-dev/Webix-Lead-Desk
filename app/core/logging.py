"""Minimal structured-event logging without sensitive request payloads."""

import logging


def configure_logging() -> None:
    """Enable concise operational logs and suppress SQL parameter logging."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
