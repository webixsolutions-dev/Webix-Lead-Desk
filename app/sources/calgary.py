"""City of Calgary licensed-business discovery; no website/email assumptions."""

import json
import re
from collections.abc import Callable
from urllib.parse import urlencode

from app.integrations.http import fetch_calgary
from app.sources.validation import Candidate

SOURCE_URL = (
    "https://data.calgary.ca/Business-and-Economic-Activity/Calgary-Business-Licenses/vdjc-pybd"
)


def discover(
    category: str, limit: int = 50, *, fetch: Callable[[str], bytes] = fetch_calgary
) -> list[Candidate]:
    """Get a bounded set of currently licensed candidates using a safe query value."""
    category = category.strip().upper()
    if not re.fullmatch(r"[A-Z0-9 &/()-]{2,80}", category) or not 1 <= limit <= 100:
        raise ValueError("Use a business category and a limit from 1 to 100")
    query = urlencode(
        {
            "$where": f"jobstatusdesc='Licensed' AND upper(licencetypes) like '%{category}%'",
            "$limit": str(limit),
            "$order": "getbusid",
        }
    )
    payload = json.loads(fetch("/resource/vdjc-pybd.json?" + query))
    if not isinstance(payload, list) or len(payload) > limit:
        raise ValueError("City data response is invalid")
    return [
        Candidate(
            name=item["tradename"],
            city="Calgary",
            industry=item["licencetypes"],
            address=item.get("address", ""),
            source_key=str(item["getbusid"]),
            source_url=SOURCE_URL,
        )
        for item in payload
        if item.get("tradename") and item.get("getbusid")
    ]
