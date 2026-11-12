"""Technical evidence and review suggestions for public business pages."""

import re
from collections.abc import Callable
from typing import Any

from app.audit.provider_pause import pause, paused_event
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.delivery_models import IntegrationSecret
from app.db.session import Database
from app.integrations.pagespeed import PageSpeedUnavailable, mobile_audit
from app.integrations.public_http import Page


def seo_review(facts: dict[str, Any], robots_header: str) -> list[str]:
    """Flag concrete markup worth reviewing before proposing an SEO service."""
    points = []
    directives = " ".join([robots_header, *facts["robots_meta"].values()])
    if re.search(r"(?<![\w-])noindex(?![\w-])", directives, re.I):
        points.append("SEO review: confirm whether the observed noindex directive is intentional.")
    if not facts["title_present"]:
        points.append("SEO review: the fetched HTML has no page title. Confirm the rendered page.")
    return points


def speed_review(facts: dict[str, Any]) -> list[str]:
    """Flag a weak lab observation without inferring visitor behavior or cause."""
    if facts.get("status") != "measured":
        return []
    points = []
    lcp = facts.get("lcp_ms")
    if isinstance(lcp, int | float) and lcp > 4000:
        points.append(f"Speed review: mobile lab LCP was {lcp / 1000:.1f} seconds on this page.")
    cls = facts.get("cls")
    if isinstance(cls, int | float) and cls > 0.25:
        points.append(f"Layout review: mobile lab layout shift (CLS) was {cls:.3f} on this page.")
    return points


def performance(
    db: Database,
    settings: Settings | None,
    page: Page,
    request_id: str,
    provider: Callable[[str, str], dict[str, Any]] = mobile_audit,
) -> dict[str, Any]:
    """Return one mobile result or an explicit reason the lab test is unavailable."""
    if settings is None:
        return {"status": "not configured"}
    with db.sessions() as session:
        if paused_event(session, "pagespeed"):
            return {"status": "paused after provider failure"}
        if not session.get(IntegrationSecret, "pagespeed"):
            return {"status": "not configured"}
    try:
        key = read_secret(db, settings, "pagespeed")
    except ValueError:
        return {"status": "credential needs review"}
    try:
        return provider(page.url, key)
    except PageSpeedUnavailable:
        with db.sessions.begin() as session:
            pause(session, "pagespeed", request_id)
        return {"status": "provider unavailable; automatic requests paused"}
