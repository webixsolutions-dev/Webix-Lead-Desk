"""Single no-retry OpenAI Responses adapter with fixed endpoint and bounded output."""

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from app.integrations.http import NoRedirect


class ModelUnavailable(Exception):
    """A model call did not provide a complete usable result; billing may be uncertain."""


def create_response(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call the fixed provider once; never follow redirects or retry uncertain requests."""
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=45) as response:
            raw = response.read(256_001)
        if len(raw) > 256_000:
            raise ModelUnavailable("Provider response exceeds the size limit")
        parsed: object = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ModelUnavailable("Provider response was not an object")
        return parsed
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise ModelUnavailable("Provider unavailable; review the reserved call") from error
