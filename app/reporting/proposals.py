"""Proposal tasks for the Command Center, derived solely from stored records."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.booking_models import Booking
from app.db.lead_models import Company
from app.db.proposal_models import Proposal, ProposalFollowup


def proposal_work(session: Session, now: datetime) -> list[dict[str, Any]]:
    """Expose unfinished proposals and day 2/5/9 review reminders without sending."""
    from app.proposals.followups import hold_reason
    from app.reporting.work_queue import item

    result: list[dict[str, Any]] = []
    for booking, name in session.execute(
        select(Booking, Company.name)
        .join(Company)
        .where(Booking.state == "completed", ~Booking.id.in_(select(Proposal.booking_id)))
    ):
        result.append(
            item(
                "proposal-notes:" + booking.id,
                name,
                "Confirm meeting notes and prepare proposal",
                "/bookings/" + booking.id + "/pack",
                "Proposal",
            )
        )
    latest = (
        select(Proposal.booking_id, func.max(Proposal.version).label("version"))
        .group_by(Proposal.booking_id)
        .subquery()
    )
    for proposal in session.scalars(
        select(Proposal)
        .join(
            latest,
            (latest.c.booking_id == Proposal.booking_id) & (latest.c.version == Proposal.version),
        )
        .where(Proposal.state.in_(["draft", "approved"]))
    ):
        result.append(
            item(
                "proposal:" + proposal.id,
                proposal.snapshot["company"],
                "Review proposal"
                if proposal.state == "draft"
                else "Approved proposal awaiting delivery record",
                "/proposals/" + proposal.id,
                "Proposal",
                proposal.created_at,
            )
        )
    for task, proposal in session.execute(
        select(ProposalFollowup, Proposal)
        .join(Proposal)
        .where(ProposalFollowup.state == "pending", ProposalFollowup.due_at <= now)
    ):
        result.append(
            item(
                "proposal-followup:" + task.id,
                proposal.snapshot["company"],
                hold_reason(session, proposal)
                or f"Day {task.day}: review conversation before any follow-up",
                "/proposals/" + proposal.id,
                "Proposal",
                task.due_at,
            )
        )
    return result
