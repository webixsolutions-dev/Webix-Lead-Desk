"""Human-reviewed partner targeting and a global five-new-business daily cap."""

from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.lead_models import Campaign, Company
from app.db.models import AuditEvent, User
from app.db.partner_models import PartnerDay, PartnerReview, PartnerSlot
from app.db.session import Database
from app.sending.window import CALGARY
from app.sources.validation import website_url


def partner_block(session: Session, company_id: str, campaign: Campaign) -> str | None:
    """Require fresh owner review for the campaign's country without weakening contact rules."""
    if campaign.lane != "partner":
        return None
    review = session.get(PartnerReview, company_id)
    actor = session.get(User, review.reviewed_by) if review else None
    if (
        not review
        or review.country != campaign.country
        or review.reviewed_at < utc_now() - timedelta(days=30)
        or not actor
        or not actor.active
        or actor.role != "owner"
    ):
        return "Partner country and cross-border permission review is missing or expired"
    return None


def reserve_slot(session: Session, company_id: str, campaign: Campaign, now: datetime) -> None:
    """Atomically reserve at most five new partner businesses per Calgary day across campaigns."""
    if campaign.lane != "partner":
        return
    if reason := partner_block(session, company_id, campaign):
        raise ValueError(reason)
    if session.get(PartnerSlot, company_id):
        return
    day = now.astimezone(CALGARY).date().isoformat()
    if not session.get(PartnerDay, day):
        try:
            with session.begin_nested():
                session.add(PartnerDay(local_day=day, reserved=0))
                session.flush()
        except IntegrityError:
            if not session.get(PartnerDay, day):
                raise
    claimed = session.scalar(
        update(PartnerDay)
        .where(PartnerDay.local_day == day, PartnerDay.reserved < 5)
        .values(reserved=PartnerDay.reserved + 1)
        .returning(PartnerDay.local_day)
    )
    if not claimed:
        raise ValueError(
            "Five new partner businesses are already reserved today; review them first"
        )
    session.add(PartnerSlot(company_id=company_id, local_day=day))
    session.flush()


def review_partner(
    db: Database, company_id: str, country: str, source_url: str, note: str, actor_id: str
) -> None:
    """Record an owner's explicit jurisdiction review; no email approval is implied."""
    url = website_url(source_url)
    if country not in {"CA", "US"} or not url or not 20 <= len(note.strip()) <= 2000:
        raise ValueError("Record Canada/US, a public source and a specific permission review")
    with db.sessions.begin() as session:
        actor = session.get(User, actor_id)
        if (
            not actor
            or not actor.active
            or actor.role != "owner"
            or not session.get(Company, company_id)
        ):
            raise ValueError("An active owner must review an existing partner business")
        review = session.get(PartnerReview, company_id)
        if review is None:
            review = PartnerReview(company_id=company_id)
            session.add(review)
        review.country, review.source_url, review.note = country, url, note.strip()
        review.reviewed_by, review.reviewed_at = actor_id, utc_now()
        session.add(AuditEvent(actor_id=actor_id, action="partner.reviewed", entity_id=company_id))
