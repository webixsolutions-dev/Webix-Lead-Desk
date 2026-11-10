"""Owner work ordered by conversation progress, with no provider or approval side effects."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from app.core.timezones import zone
from app.db.booking_models import Booking
from app.db.delivery_models import InboxMessage, SendIntent
from app.db.lead_models import Company
from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Person, Touch
from app.db.models import AuditEvent
from app.db.outreach_models import Conversation, Draft
from app.db.pipeline_models import Opportunity
from app.db.session import Database
from app.reporting.lead_actions import next_actions

GROUPS = (
    ("replies", "Replies waiting"),
    ("meetings", "Meetings and confirmations"),
    ("proposals", "Proposals"),
    ("drafts", "Drafts to approve"),
    ("calls", "Calls due"),
    ("linkedin", "LinkedIn member tasks"),
    ("research", "Research needed"),
    ("system", "System attention"),
)


def item(
    key: str, title: str, detail: str, url: str, channel: str, at: datetime | None = None
) -> dict[str, Any]:
    """Describe a real persisted work item and its authenticated review destination."""
    return {"key": key, "title": title, "detail": detail, "url": url, "channel": channel, "at": at}


def reviewed(session: Session, kind: str) -> Any:
    """Select explicit owner acknowledgements, not inferred read receipts."""
    return select(AuditEvent.entity_id).where(AuditEvent.action == f"queue.{kind}.reviewed")


def email_replies(session: Session) -> list[dict[str, Any]]:
    """Keep the latest human message per exact sender, with unmatched senders separate."""
    ranked = (
        select(
            InboxMessage.id,
            func.row_number()
            .over(
                partition_by=[
                    InboxMessage.mailbox_id,
                    InboxMessage.conversation_id,
                    InboxMessage.sender,
                ],
                order_by=InboxMessage.received_at.desc(),
            )
            .label("rank"),
        )
        .where(InboxMessage.classification.not_in(["automatic", "bounce", "delivery_delayed"]))
        .subquery()
    )
    answered = exists(
        select(SendIntent.id)
        .join(Draft, Draft.id == SendIntent.draft_id)
        .where(
            SendIntent.conversation_id == InboxMessage.conversation_id,
            Draft.kind == "reply",
            Draft.created_at >= InboxMessage.received_at,
            Draft.snapshot["recipient"].as_string() == InboxMessage.sender,
            SendIntent.state.in_(["scheduled", "transmitting", "accepted", "uncertain"]),
        )
    )
    query = (
        select(InboxMessage, Company.name)
        .join(ranked, ranked.c.id == InboxMessage.id)
        .outerjoin(Conversation, Conversation.id == InboxMessage.conversation_id)
        .outerjoin(Company, Company.id == Conversation.company_id)
        .where(
            ranked.c.rank == 1,
            ~InboxMessage.id.in_(reviewed(session, "email")),
            ~answered,
            InboxMessage.classification.not_in(
                ["bounce", "unsubscribe", "not_interested", "automatic"]
            ),
        )
    )
    return [
        item(
            "email:" + message.id,
            name or message.sender,
            message.sender + " · " + message.classification.replace("_", " "),
            "/command/replies/email/" + message.id,
            "Email",
            message.received_at,
        )
        for message, name in session.execute(query).all()
    ]


def linkedin_replies(session: Session) -> list[dict[str, Any]]:
    """A later real outbound touch or owner acknowledgement resolves the latest reply."""
    ranked = (
        select(
            Touch.id,
            func.row_number()
            .over(partition_by=Touch.person_id, order_by=Touch.occurred_at.desc())
            .label("rank"),
        )
        .where(
            Touch.channel == "linkedin", Touch.direction == "inbound", Touch.person_id.is_not(None)
        )
        .subquery()
    )
    outbound = aliased(Touch)
    answered = exists(
        select(outbound.id).where(
            outbound.person_id == Touch.person_id,
            outbound.channel == "linkedin",
            outbound.direction == "outbound",
            outbound.occurred_at >= Touch.occurred_at,
        )
    )
    query = (
        select(Touch, Person.full_name, Company.name)
        .join(ranked, ranked.c.id == Touch.id)
        .join(Person, Person.id == Touch.person_id)
        .join(Company, Company.id == Person.company_id)
        .where(
            ranked.c.rank == 1,
            ~answered,
            ~Touch.id.in_(reviewed(session, "linkedin")),
            Touch.outcome.not_in(["unsubscribe", "not_interested", "stop"]),
        )
    )
    return [
        item(
            "linkedin:" + touch.id,
            name,
            company,
            "/command/replies/linkedin/" + touch.id,
            "LinkedIn",
            touch.occurred_at,
        )
        for touch, name, company in session.execute(query).all()
    ]


def queue(
    db: Database, now: datetime, timezone: str, calls: list[dict[str, Any]], warnings: list[str]
) -> dict[str, Any]:
    """Build ordered groups; render at most 50 rows per group while keeping full counts."""
    local = now.astimezone(zone(timezone))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    values: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in GROUPS}
    with db.sessions() as session:
        values["replies"] = sorted(
            email_replies(session) + linkedin_replies(session), key=lambda row: row["at"]
        )
        for booking, name in session.execute(
            select(Booking, Company.name)
            .join(Company)
            .where(
                (Booking.state == "proposed")
                | (
                    (Booking.state == "confirmed")
                    & (Booking.starts_at >= start)
                    & (Booking.starts_at < end)
                )
            )
            .order_by(Booking.starts_at)
        ):
            when = booking.starts_at.astimezone(zone(booking.timezone)).strftime("%d %b %H:%M")
            values["meetings"].append(
                item(
                    "booking:" + booking.id,
                    name,
                    f"{booking.state.capitalize()} · {when} · {booking.timezone} · {booking.host}",
                    "/pipeline/bookings?booking=" + booking.id,
                    "Meeting",
                    booking.starts_at,
                )
            )
        from app.reporting.proposals import proposal_work

        values["proposals"].extend(proposal_work(session, now))
        for card, name in session.execute(
            select(Opportunity, Company.name)
            .join(Conversation, Conversation.id == Opportunity.conversation_id)
            .join(Company)
            .where(
                Opportunity.stage.in_(
                    ["Call held", "Meeting held", "Proposal", "Proposal sent", "Negotiation"]
                )
            )
            .order_by(Opportunity.next_due_at.asc().nulls_first())
        ):
            values["proposals"].append(
                item(
                    "opportunity:" + card.id,
                    name,
                    card.next_action or "Prepare a reviewed proposal",
                    "/pipeline/" + card.id,
                    "Proposal",
                    card.next_due_at,
                )
            )
        for draft, conversation, name in session.execute(
            select(Draft, Conversation, Company.name)
            .join(Conversation, Conversation.id == Draft.conversation_id)
            .join(Company)
            .where(Draft.state == "draft", Draft.kind != "reply")
            .order_by(Draft.created_at)
        ):
            step = draft.sequence_step if draft.kind == "follow_up" else draft.step
            values["drafts"].append(
                item(
                    "draft:" + draft.id,
                    name,
                    draft.kind.replace("_", " ") + " · " + draft.snapshot.get("recipient", ""),
                    f"/leads/{conversation.company_id}/draft?step={step}&kind={draft.kind}",
                    "Email",
                    draft.created_at,
                )
            )
        member_tasks: dict[str, dict[str, Any]] = {}
        for task, name, profile in session.execute(
            select(LinkedInTask, Person.full_name, LinkedInProfile.display_name)
            .join(Person, Person.id == LinkedInTask.person_id)
            .join(LinkedInProfile, LinkedInProfile.id == LinkedInTask.profile_id)
            .where(LinkedInTask.state.in_(["pending", "ready", "in_progress"]))
            .order_by(LinkedInTask.due_at)
        ):
            counts = member_tasks.setdefault(
                task.profile_id, {"name": profile, "pending": 0, "approved": 0}
            )
            counts["pending"] += 1
            counts["approved"] += bool(task.approval_digest)
            if not task.approval_digest and task.action not in {"reply", "withdraw"}:
                values["drafts"].append(
                    item(
                        "task:" + task.id,
                        name,
                        task.action.replace("_", " ") + " · " + profile,
                        "/linkedin/tasks/" + task.id,
                        "LinkedIn",
                        task.due_at,
                    )
                )
        for profile, counts in member_tasks.items():
            values["linkedin"].append(
                item(
                    "member:" + profile,
                    counts["name"],
                    f"{counts['pending']} pending tasks · {counts['approved']} approved",
                    "/linkedin/plan?profile=" + profile,
                    "LinkedIn",
                )
            )
        for company in session.scalars(
            select(Company)
            .where(Company.status.in_(["needs_review", "shared_review", "research_more"]))
            .order_by(Company.created_at)
        ):
            reason = "Confirm the official website and business identity"
            if company.status == "shared_review":
                reason = "Confirm whether this shared website belongs to the business"
            values["research"].append(
                item(
                    "research:" + company.id,
                    company.name,
                    reason,
                    "/leads/" + company.id,
                    "Research",
                    company.created_at,
                )
            )
        values["research"].extend(next_actions(session, now))
        review_count = (
            session.scalar(select(func.count()).select_from(Draft).where(Draft.state == "draft"))
            or 0
        )
        review_count += (
            session.scalar(
                select(func.count())
                .select_from(LinkedInTask)
                .where(
                    LinkedInTask.state.in_(["pending", "ready"]),
                    LinkedInTask.approval_digest.is_(None),
                    LinkedInTask.action != "withdraw",
                )
            )
            or 0
        )
        week_start = start - timedelta(days=start.weekday())
        week_end = week_start + timedelta(days=7)
        meeting_count = (
            session.scalar(
                select(func.count())
                .select_from(Booking)
                .where(
                    Booking.state == "confirmed",
                    Booking.starts_at >= week_start,
                    Booking.starts_at < week_end,
                )
            )
            or 0
        )
    for card in calls:
        company = card["company"]
        values["calls"].append(
            item(
                "call:" + company.id,
                company.name,
                company.phone,
                "/leads/calls?company=" + company.id,
                "Call",
            )
        )
    for index, warning in enumerate(warnings):
        values["system"].append(
            item(
                "system:" + str(index),
                "Review workspace setup",
                warning,
                "/settings#operations",
                "System",
            )
        )
    groups: list[dict[str, Any]] = [
        {"key": key, "label": label, "count": len(values[key]), "items": values[key][:50]}
        for key, label in GROUPS
    ]
    for group in groups:
        for row in group["items"]:
            age = max(0, int((now - row["at"]).total_seconds() // 60)) if row["at"] else None
            row["age"] = f"{age // 60}h {age % 60}m ago" if age is not None else ""
    return {
        "work_groups": groups,
        "work_count": sum(group["count"] for group in groups),
        "reply_count": len(values["replies"]),
        "review_count": review_count,
        "meetings_week": meeting_count,
    }
