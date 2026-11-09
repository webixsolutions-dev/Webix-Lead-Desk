"""Pinned civil-time rules are independent of the worker host's installed tz database."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfoNotFoundError

import pytest

from app.core.timezones import zone
from app.sending.window import within_window


def test_alberta_time_uses_current_bundled_rules() -> None:
    """November 2026 stays UTC-6, while historical winter dates retain UTC-7."""
    calgary = zone("America/Edmonton")
    assert datetime(2025, 1, 6, tzinfo=calgary).utcoffset() == timedelta(hours=-7)
    assert datetime(2026, 11, 9, tzinfo=calgary).utcoffset() == timedelta(hours=-6)
    assert datetime(2027, 1, 4, tzinfo=calgary).utcoffset() == timedelta(hours=-6)
    assert within_window(datetime(2026, 11, 9, 14, 30, tzinfo=UTC))
    assert not within_window(datetime(2026, 11, 9, 17, 30, tzinfo=UTC))


@pytest.mark.parametrize(
    "name", ["../settings", "/etc/passwd", "America/../UTC", "Unknown/Place", ""]
)
def test_timezone_resource_paths_are_validated(name: str) -> None:
    """User timezone preferences cannot traverse outside bundled public timezone data."""
    with pytest.raises(ZoneInfoNotFoundError):
        zone(name)
