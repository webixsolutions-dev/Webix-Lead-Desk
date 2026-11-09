"""Atomic provider call, monthly cost and overall AI budget reservations."""

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.budget import BudgetBlocked, reserve_in_session, settle
from app.core.timezones import zone
from app.db.acquisition_models import SearchBucket, SearchRecord
from app.db.session import Database
from app.sources.enrichment.config import SearchConfig


def ensure_bucket(session: Session, key: str) -> None:
    """Create an empty bucket without racing another transaction's insert."""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    session.execute(
        insert(SearchBucket)
        .values(key=key, calls=0, committed=0)
        .on_conflict_do_nothing(index_elements=["key"])
    )


def bucket_keys(provider: str, now: datetime) -> tuple[str, str]:
    """Use Calgary calendar boundaries, including daylight-saving transitions."""
    local = now.astimezone(zone("America/Edmonton"))
    return f"{provider}:day:{local:%Y-%m-%d}", f"{provider}:month:{local:%Y-%m}"


def reserve_search(
    db: Database,
    config: SearchConfig,
    key: str,
    cache_key: str,
    company_id: str | None,
    campaign_id: str,
    model: str,
    maximum: int,
    now: datetime,
) -> SearchRecord:
    """Reserve both limits in one transaction, serializing concurrent cache checks."""
    places = config.provider == "google_places"
    daily = config.places_daily_calls if places else config.daily_calls
    monthly = config.places_monthly_calls if places else config.monthly_calls
    money = config.places_monthly_usd_micro if places else config.monthly_usd_micro
    if (
        not config.enabled
        or (places and not config.places_enabled)
        or min(daily, monthly, money, maximum) <= 0
    ):
        raise BudgetBlocked("Configure and enable owner search quotas and prices first")
    day_key, month_key = bucket_keys(config.provider, now)
    try:
        with db.sessions.begin() as session:
            lock = "cache:" + hashlib.sha256(cache_key.encode()).hexdigest()
            ensure_bucket(session, lock)
            session.execute(
                update(SearchBucket)
                .where(SearchBucket.key == lock)
                .values(calls=SearchBucket.calls)
            )
            # All search reservations for a provider serialize on the month row before
            # checking the 90-day marker. Failed downstream reservations roll back both.
            for bucket, cap in ((month_key, monthly), (day_key, daily)):
                ensure_bucket(session, bucket)
                changed = session.scalar(
                    update(SearchBucket)
                    .where(
                        SearchBucket.key == bucket,
                        SearchBucket.calls < cap,
                        SearchBucket.committed + maximum <= money,
                    )
                    .values(
                        calls=SearchBucket.calls + 1, committed=SearchBucket.committed + maximum
                    )
                    .returning(SearchBucket.key)
                )
                if not changed:
                    raise BudgetBlocked("Daily/monthly search quota or monthly cost cap reached")
            if session.scalar(
                select(SearchRecord.id)
                .where(
                    SearchRecord.cache_key == cache_key,
                    SearchRecord.created_at > now - timedelta(days=90 if company_id else 1),
                )
                .limit(1)
            ):
                raise BudgetBlocked(
                    (
                        "Already searched within 90 days; "
                        "use the stored result or review the reservation"
                    )
                    if company_id
                    else "This discovery query ran within 24 hours; review the saved results"
                )
            call = None if places else reserve_in_session(session, key, model, maximum, now)
            record = SearchRecord(
                request_key=key,
                cache_key=cache_key,
                company_id=company_id,
                campaign_id=campaign_id,
                provider=config.provider,
                reserved=maximum,
                ai_call_id=call.id if call else None,
                created_at=now,
            )
            session.add(record)
            session.flush()
            return record
    except IntegrityError as error:
        raise BudgetBlocked(
            "A concurrent search reserved this request; inspect stored results"
        ) from error


def settle_search(db: Database, record: SearchRecord, actual: int | None) -> None:
    """Release only measured unused cost; unknown outcomes keep the whole reservation."""
    if actual is not None and not 0 <= actual <= record.reserved:
        actual = None
    if record.ai_call_id:
        settle(db, record.ai_call_id, actual)
    with db.sessions.begin() as session:
        changed = session.scalar(
            update(SearchRecord)
            .where(SearchRecord.id == record.id, SearchRecord.state == "reserved")
            .values(state="complete" if actual is not None else "uncertain")
            .returning(SearchRecord.id)
        )
        if changed and actual is not None:
            for key in bucket_keys(record.provider, record.created_at):
                session.execute(
                    update(SearchBucket)
                    .where(SearchBucket.key == key)
                    .values(committed=SearchBucket.committed - (record.reserved - actual))
                )
