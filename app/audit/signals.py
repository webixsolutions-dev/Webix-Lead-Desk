"""Code-first evidence extraction without browser execution or hidden provider requests."""

import re
from typing import Any

from app.playbook.industries import catalog


def tool_signals(html: str, links: list[str]) -> dict[str, Any]:
    """Record public tool mentions and booking links as leads for verification only."""
    names: set[str] = set()
    for item in catalog():
        match = re.search(r"\*\*Tools to detect:\*\* ([^\n]+)", item["guidance"])
        if match:
            names.update(name.strip().rstrip(".") for name in match[1].split(","))
    mentions = sorted(
        name for name in names if len(name) > 3 and re.search(re.escape(name), html, re.I)
    )[:30]
    return {
        "tool_mentions": mentions,
        "booking_links": [
            link
            for link in links
            if re.search(r"book|appointment|schedule|calendly|fresha|janeapp", link, re.I)
        ][:12],
        "tool_status": "Public mentions only; integration, configuration and adequacy unverified",
        "social_recency": "not checked",
        "ads_library": "not checked",
        "maps_reviews": "not checked",
    }
