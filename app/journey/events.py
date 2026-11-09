"""Transactional append-only facts shared by all acquisition modules."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.acquisition_models import LeadEvent
from app.db.models import new_id


def emit(
    session: Session,
    company_id: str,
    key: str,
    kind: str,
    data: dict[str, Any] | None = None,
    *,
    actor: str | None = None,
    at: datetime | None = None,
    url: str = "",
) -> None:
    """Insert one event with an idempotency key in the caller's transaction."""
    values = dict(
        id=new_id(),
        company_id=company_id,
        event_key=key,
        kind=kind,
        data=data or {},
        actor_id=actor,
        occurred_at=at or utc_now(),
        item_url=url or f"/leads/{company_id}",
    )
    if session.scalar(select(LeadEvent.id).where(LeadEvent.event_key == key)):
        return
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    session.execute(
        insert(LeadEvent).values(**values).on_conflict_do_nothing(index_elements=["event_key"])
    )
