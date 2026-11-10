"""Timezone-aware proposals with serialized owner confirmation and overlap checks."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from app.core.clock import utc_now
from app.core.timezones import zone
from app.db.booking_models import Booking
from app.db.lead_models import Company
from app.db.models import AuditEvent, User
from app.db.session import Database


def local_start(value: str, timezone: str) -> datetime:
    """Reject nonexistent and ambiguous local times rather than guessing a DST offset."""
    try:
        naive = datetime.fromisoformat(value)
        tz = zone(timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("Choose a date/time and a valid IANA timezone") from error
    if naive.tzinfo:
        raise ValueError("Enter local time without an offset")
    first, second = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    if (
        first.utcoffset() != second.utcoffset()
        or first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive
    ):
        raise ValueError(
            "This time crosses a daylight-saving transition; choose an unambiguous time"
        )
    return first.astimezone(UTC)


def save(db: Database, actor_id: str, fields: dict[str, str]) -> Booking:
    """Create or edit a proposal; every material edit removes confirmation."""
    start = local_start(fields.get("starts_at", ""), fields.get("timezone", ""))
    minutes = int(fields.get("minutes", "30"))
    if not 10 <= minutes <= 240 or start <= utc_now():
        raise ValueError("Choose a future meeting lasting 10–240 minutes")
    mode = fields.get("mode", "video")
    values = {
        key: fields.get(key, "").strip() for key in ("title", "contact", "host", "location", "note")
    }
    if (
        mode not in {"video", "phone", "in_person"}
        or any(
            not 2 <= len(values[key]) <= limit
            for key, limit in (("title", 160), ("contact", 254), ("host", 160))
        )
        or len(values["location"]) > 2000
        or len(values["note"]) > 2000
    ):
        raise ValueError("Provide the meeting title, actual contact and host, with bounded notes")
    with db.sessions.begin() as session:
        owner = session.get(User, actor_id)
        if not owner or owner.role != "owner" or not owner.active:
            raise ValueError("Only the owner can manage bookings")
        if not session.get(Company, fields.get("company_id", "")):
            raise ValueError("Choose an existing business")
        item = session.get(Booking, fields.get("booking_id", ""))
        if item:
            changed = cast(
                CursorResult[Any],
                session.execute(
                    update(Booking)
                    .where(
                        Booking.id == item.id, Booking.version == int(fields.get("version", "0"))
                    )
                    .values(version=Booking.version + 1)
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("Booking changed; reload it")
        elif fields.get("booking_id"):
            raise ValueError("Booking not found")
        else:
            item = Booking(created_by=actor_id)
            session.add(item)
        item.company_id = fields["company_id"]
        item.title, item.contact, item.host = values["title"], values["contact"], values["host"]
        item.starts_at, item.ends_at = start, start + timedelta(minutes=minutes)
        item.timezone, item.mode = fields["timezone"], mode
        item.location, item.note = values["location"], values["note"]
        item.state, item.confirmed_by = "proposed", None
        session.flush()
        from app.bookings.packs import prepare

        prepare(session, item)
        session.add(AuditEvent(actor_id=actor_id, action="booking.proposed", entity_id=item.id))
        return item


def decide(db: Database, actor_id: str, booking_id: str, version: int, action: str) -> None:
    """Confirm only checked availability; never send calendar invitations or emails."""
    if action not in {"confirmed", "cancelled", "completed"}:
        raise ValueError("Choose a supported booking action")
    with db.sessions.begin() as session:
        # All owner booking decisions share this row lock, including cross-owner conflicts.
        session.execute(
            select(User).where(User.role == "owner").order_by(User.id).with_for_update()
        ).all()
        owner = session.get(User, actor_id)
        item = session.get(Booking, booking_id)
        if (
            not owner
            or owner.role != "owner"
            or not owner.active
            or not item
            or item.version != version
        ):
            raise ValueError("Booking changed or owner unavailable; reload")
        if action == "confirmed":
            if item.state != "proposed" or item.starts_at <= utc_now() or not item.location:
                raise ValueError(
                    "Confirm a future proposal with a real meeting link/location or phone"
                )
            conflict = session.scalar(
                select(Booking.id)
                .where(
                    Booking.id != item.id,
                    Booking.state == "confirmed",
                    func.lower(Booking.host) == item.host.lower(),
                    Booking.starts_at < item.ends_at,
                    Booking.ends_at > item.starts_at,
                )
                .limit(1)
            )
            if conflict:
                raise ValueError("This host already has an overlapping confirmed booking")
            item.confirmed_by = actor_id
        if action == "completed" and (item.state != "confirmed" or item.ends_at > utc_now()):
            raise ValueError("Only a confirmed past booking can be completed")
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(Booking)
                .where(Booking.id == booking_id, Booking.version == version)
                .values(state=action, version=version + 1)
            ),
        ).rowcount
        if changed != 1:
            raise ValueError("Booking changed; reload")
        if action == "completed":
            from app.bookings.pipeline import meeting_held

            meeting_held(session, item, actor_id)
        session.add(AuditEvent(actor_id=actor_id, action="booking." + action, entity_id=booking_id))
