"""Review a small source-backed cohort without turning a launch decision into email approval."""

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.delivery_models import SendIntent
from app.db.lead_models import Company, CompanyDomain
from app.db.models import AuditEvent, User
from app.db.outreach_models import Conversation
from app.db.pilot_models import Pilot, PilotMember, PilotReservation
from app.db.research_models import Evidence
from app.db.session import Database
from app.sources.validation import domain_of

SECTORS = {"HVAC": 4, "Roofing": 4, "Cleaning": 2}


def _owner(session: Session, actor_id: str) -> None:
    actor = session.get(User, actor_id)
    if not actor or not actor.active or actor.role != "owner":
        raise ValueError("Only the owner can review the pilot")


def candidate_blocks(session: Session, company: Company) -> list[str]:
    """Require a matched public website and recent recorded evidence before cohort review."""
    reasons: list[str] = []
    if not company.website or not session.scalar(
        select(CompanyDomain.id).where(
            CompanyDomain.company_id == company.id,
            CompanyDomain.domain == domain_of(company.website),
            CompanyDomain.status == "verified",
        )
    ):
        reasons.append("Website match needs review")
    if not session.scalar(
        select(Evidence.id)
        .where(
            Evidence.company_id == company.id,
            Evidence.observed_at >= utc_now() - timedelta(days=30),
        )
        .limit(1)
    ):
        reasons.append("Recent website evidence is missing")
    return reasons


def save_member(db: Database, company_id: str, sector: str, note: str, actor_id: str) -> None:
    """Save one review with a clear conflict message for concurrent initial setup."""
    try:
        _save_member(db, company_id, sector, note, actor_id)
    except IntegrityError as error:
        raise ValueError("The pilot changed concurrently; reload before reviewing") from error


def _save_member(db: Database, company_id: str, sector: str, note: str, actor_id: str) -> None:
    """Add or replace one reviewed candidate only before the pilot is activated."""
    if sector not in SECTORS or not 20 <= len(note.strip()) <= 1000:
        raise ValueError("Choose a pilot sector and record a specific 20–1000 character review")
    with db.sessions.begin() as session:
        _owner(session, actor_id)
        pilot = session.get(Pilot, "primary")
        existing_member = session.get(PilotMember, company_id)
        if (
            pilot
            and pilot.state != "draft"
            and (pilot.state != "paused" or not existing_member or existing_member.sector != sector)
        ):
            raise ValueError("The active cohort is frozen; pause it to refresh existing reviews")
        if pilot is None:
            pilot = Pilot(key="primary", updated_by=actor_id)
            session.add(pilot)
            session.flush()
        # Serialize concurrent cohort edits without relying on SQLite-only locking.
        changed = session.scalar(
            update(Pilot)
            .where(
                Pilot.key == "primary", Pilot.state == pilot.state, Pilot.version == pilot.version
            )
            .values(version=Pilot.version + 1)
            .returning(Pilot.key)
        )
        if not changed:
            raise ValueError("The pilot changed concurrently; reload before editing")
        company = session.get(Company, company_id)
        if not company:
            raise ValueError("Choose an imported business")
        blocks = candidate_blocks(session, company)
        if blocks:
            raise ValueError("; ".join(blocks))
        count = (
            session.scalar(
                select(func.count())
                .select_from(PilotMember)
                .where(PilotMember.sector == sector, PilotMember.company_id != company_id)
            )
            or 0
        )
        if count >= SECTORS[sector]:
            raise ValueError("This sector's pilot places are full")
        member = session.get(PilotMember, company_id)
        if member is None:
            member = PilotMember(
                company_id=company_id, sector=sector, review_note=note.strip(), reviewed_by=actor_id
            )
            session.add(member)
        else:
            member.sector, member.review_note = sector, note.strip()
            member.reviewed_by, member.reviewed_at = actor_id, utc_now()
        session.add(
            AuditEvent(actor_id=actor_id, action="pilot.member_reviewed", entity_id=company_id)
        )


def transition(db: Database, target: str, expected_version: int, note: str, actor_id: str) -> None:
    """Activate, pause or complete only an owner-reviewed pilot using optimistic concurrency."""
    if target not in {"active", "paused", "complete"} or not 20 <= len(note.strip()) <= 2000:
        raise ValueError("Record a specific acceptance or pause note")
    with db.sessions.begin() as session:
        _owner(session, actor_id)
        pilot = session.get(Pilot, "primary")
        if not pilot or pilot.version != expected_version:
            raise ValueError("Pilot changed; reload before updating it")
        allowed = {"draft": {"active"}, "active": {"paused", "complete"}, "paused": {"active"}}
        if target not in allowed.get(pilot.state, set()):
            raise ValueError("This pilot transition is unavailable")
        if target == "active":
            members = list(session.scalars(select(PilotMember)))
            if {
                sector: sum(member.sector == sector for member in members) for sector in SECTORS
            } != SECTORS:
                raise ValueError("Review 4 HVAC, 4 roofing and 2 cleaning companies first")
            for member in members:
                company = session.get(Company, member.company_id)
                if (
                    not company
                    or candidate_blocks(session, company)
                    or member.reviewed_at < utc_now() - timedelta(days=30)
                ):
                    raise ValueError("A pilot business needs fresh evidence and review")
        if target == "complete":
            uncertain = session.scalar(
                select(SendIntent.id)
                .join(Conversation, Conversation.id == SendIntent.conversation_id)
                .join(PilotMember, PilotMember.company_id == Conversation.company_id)
                .where(SendIntent.state.in_(["uncertain", "transmitting"]))
                .limit(1)
            )
            accepted = session.scalar(
                select(SendIntent.id)
                .join(PilotReservation, PilotReservation.intent_id == SendIntent.id)
                .where(SendIntent.state == "accepted")
                .limit(1)
            )
            if uncertain or not accepted:
                raise ValueError(
                    "Confirm at least one accepted pilot send and resolve uncertain sends first"
                )
        changed = session.scalar(
            update(Pilot)
            .where(Pilot.key == "primary", Pilot.version == expected_version)
            .values(
                state=target,
                version=Pilot.version + 1,
                acceptance_note=note.strip(),
                updated_by=actor_id,
                updated_at=utc_now(),
            )
            .returning(Pilot.key)
        )
        if not changed:
            raise ValueError("Pilot changed; reload before updating it")
        session.add(AuditEvent(actor_id=actor_id, action="pilot." + target, entity_id="primary"))


def gate(session: Session, company_id: str, step: int) -> str | None:
    """Require pilot acceptance; an active pilot allows only its ten selected businesses."""
    pilot = session.get(Pilot, "primary")
    if not pilot or pilot.state not in {"active", "complete"}:
        return "pilot_not_active"
    if pilot.state == "complete":
        return None
    if not session.get(PilotMember, company_id):
        return "outside_pilot_cohort"
    if step == 0 and pilot.reserved >= 5 and not session.get(PilotReservation, company_id):
        return "pilot_first_touch_limit"
    return None


def reserve_pilot(session: Session, item: SendIntent, company_id: str) -> bool:
    """Atomically consume one pilot place; retries never consume another place or bypass the cap."""
    pilot = session.get(Pilot, "primary")
    if pilot and pilot.state == "complete":
        return True
    if gate(session, company_id, item.step):
        return False
    if item.step != 0 or session.get(PilotReservation, company_id) is not None:
        return True
    changed = session.scalar(
        update(Pilot)
        .where(Pilot.key == "primary", Pilot.state == "active", Pilot.reserved < 5)
        .values(reserved=Pilot.reserved + 1)
        .returning(Pilot.key)
    )
    if not changed:
        return False
    session.add(PilotReservation(company_id=company_id, intent_id=item.id))
    return True


def cohort(db: Database) -> dict[str, Any]:
    """Return real pilot state and reviewed candidates for the private dashboard."""
    with db.sessions() as session:
        return {
            "pilot": session.get(Pilot, "primary"),
            "pilot_sectors": SECTORS,
            "pilot_members": list(session.execute(select(PilotMember, Company).join(Company))),
            "pilot_candidates": list(
                session.scalars(select(Company).order_by(Company.name).limit(500))
            ),
        }


def remove_member(db: Database, company_id: str, actor_id: str) -> None:
    """Remove a candidate before activation while retaining the business and audit history."""
    with db.sessions.begin() as session:
        _owner(session, actor_id)
        changed = session.scalar(
            update(Pilot)
            .where(Pilot.key == "primary", Pilot.state == "draft")
            .values(version=Pilot.version + 1)
            .returning(Pilot.key)
        )
        if not changed:
            raise ValueError("Only an unlaunched cohort can change its membership")
        session.execute(delete(PilotMember).where(PilotMember.company_id == company_id))
        session.add(
            AuditEvent(actor_id=actor_id, action="pilot.member_removed", entity_id=company_id)
        )
