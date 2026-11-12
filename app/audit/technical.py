"""Technical evidence about public pages, with an optional mobile lab measurement."""

from collections.abc import Callable
from typing import Any

from app.audit.provider_pause import pause, paused_event
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.delivery_models import IntegrationSecret
from app.db.session import Database
from app.integrations.pagespeed import PageSpeedUnavailable, mobile_audit
from app.integrations.public_http import Page


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
