"""Timezone-aware clock shared by services and deterministic tests."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)
