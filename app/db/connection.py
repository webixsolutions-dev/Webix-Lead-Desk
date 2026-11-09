"""Bounded connection establishment retries; never replay a transaction or a send."""

import time
from typing import Any

import psycopg
from sqlalchemy import Dialect
from sqlalchemy.pool import ConnectionPoolEntry


def transient(error: psycopg.OperationalError) -> bool:
    """Recognize transport/startup failures, excluding authentication and configuration errors."""
    code = error.sqlstate
    return code is None or code.startswith("08") or code in {"57P01", "57P02", "57P03", "53300"}


def connect_with_backoff(
    dialect: Dialect, record: ConnectionPoolEntry, args: list[Any], params: dict[str, Any]
) -> Any:
    """Retry only opening a new socket, before any application statement is executed."""
    for attempt in range(3):
        try:
            return dialect.connect(*args, **params)
        except psycopg.OperationalError as error:
            if not transient(error) or attempt == 2:
                raise
            time.sleep(0.25 * 2**attempt)
    raise AssertionError("Unreachable connection retry")
