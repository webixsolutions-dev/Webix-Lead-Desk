"""Optional Google Places New adapter: fixed endpoint, field mask and no retries."""

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from app.integrations.http import NoRedirect
from app.integrations.openai import ModelUnavailable

FIELDS = ",".join(
    "places." + field
    for field in (
        "id",
        "displayName",
        "formattedAddress",
        "websiteUri",
        "nationalPhoneNumber",
        "rating",
        "userRatingCount",
        "googleMapsUri",
    )
)


def search_places(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return transient results; the acquisition service enforces restricted retention."""
    request = Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELDS,
        },
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(128_001)
        if len(raw) > 128_000:
            raise ModelUnavailable("Places result exceeds the limit")
        result: object = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object")
        return result
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise ModelUnavailable("Places unavailable; search reservation retained") from error
