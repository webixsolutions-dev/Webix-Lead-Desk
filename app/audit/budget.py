"""Atomic AI budget reservations; no network call is allowed before reservation."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.research_models import AICall, BudgetAccount
from app.db.session import Database


class BudgetBlocked(Exception):
    """Funding, expiry, usage or an existing uncertain call prevents another request."""


def reserve(
    db: Database,
    key: str,
    model: str,
    maximum: int,
    now: datetime,
    *,
    audit: bool = True,
    category: str = "reserve",
    company_id: str | None = None,
) -> AICall:
    """Reserve a worst-case USD-micro cost once, pausing research at 90% of allowance."""
    if not 0 < maximum <= 1_000_000_000 or not model or len(key) > 128:
        raise BudgetBlocked("Invalid model rate or maximum cost")
    try:
        with db.sessions.begin() as session:
            return reserve_in_session(
                session,
                key,
                model,
                maximum,
                now,
                audit=audit,
                category=category,
                company_id=company_id,
            )
    except IntegrityError as error:
        raise BudgetBlocked("Request was already reserved") from error


def reserve_in_session(
    session: Session,
    key: str,
    model: str,
    maximum: int,
    now: datetime,
    *,
    audit: bool = True,
    category: str = "reserve",
    company_id: str | None = None,
) -> AICall:
    """Reserve within a caller transaction so provider quotas and funds commit together."""
    if not 0 < maximum <= 1_000_000_000 or not model or len(key) > 128:
        raise BudgetBlocked("Invalid model rate or maximum cost")
    if session.scalar(select(AICall).where(AICall.request_key == key)):
        raise BudgetBlocked("Request already reserved; reconcile it before any retry")
    limit = BudgetAccount.allowance * (0.9 if audit else 1)
    changed = session.scalar(
        update(BudgetAccount)
        .where(
            BudgetAccount.key == "primary",
            BudgetAccount.expires_at > now,
            BudgetAccount.committed + maximum <= limit,
        )
        .values(committed=BudgetAccount.committed + maximum)
        .returning(BudgetAccount.key)
    )
    if not changed:
        raise BudgetBlocked("Verified funding is missing, expired or at its spending limit")
    from app.audit.monthly import check

    check(session, category, maximum, now)
    call = AICall(
        request_key=key,
        model=model,
        reserved=maximum,
        state="reserved",
        category=category,
        company_id=company_id,
        created_at=now,
    )
    session.add(call)
    session.flush()
    return call


def settle(db: Database, call_id: str, actual: int | None, result: str | None = None) -> None:
    """Release only proven unused cost; an unknown provider outcome holds its full reserve."""
    with db.sessions.begin() as session:
        call = session.get(AICall, call_id)
        if call is None or call.state != "reserved":
            return
        if actual is not None and (actual < 0 or actual > call.reserved):
            actual = None  # Unverified rates or usage must never create a false refund.
        state = "complete" if actual is not None else "uncertain"
        changed = session.scalar(
            update(AICall)
            .where(
                AICall.id == call_id,
                AICall.state == "reserved",
            )
            .values(state=state, charged=actual, result=result)
            .returning(AICall.id)
        )
        if changed and actual is not None:
            session.execute(
                update(BudgetAccount)
                .where(BudgetAccount.key == "primary")
                .values(committed=BudgetAccount.committed - (call.reserved - actual))
            )
