"""Use the locked tzdata package consistently instead of a stale host timezone database."""

from functools import lru_cache
from importlib.resources import files
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@lru_cache(maxsize=128)
def zone(name: str) -> ZoneInfo:
    """Load an IANA zone from the deployed package, with safe resource path validation."""
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts) or "\\" in name or "\x00" in name:
        raise ZoneInfoNotFoundError("Invalid timezone name")
    try:
        with files("tzdata.zoneinfo").joinpath(*parts).open("rb") as source:
            return ZoneInfo.from_file(source, key=name)
    except (OSError, ValueError) as error:
        raise ZoneInfoNotFoundError("Timezone not found in bundled data") from error
