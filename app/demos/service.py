"""Immutable concept revisions and explicit publication; source text is always inert data."""

import secrets
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.delivery_models import SendIntent
from app.db.lead_models import Company
from app.db.models import AuditEvent, User
from app.db.outreach_models import Conversation, Draft
from app.db.pipeline_models import DemoVersion, Opportunity, PipelineEvent
from app.db.research_models import Evidence
from app.db.session import Database
from app.drafting.content import fingerprint
from app.pipeline.service import QUALIFIED

KINDS = {
    "homepage": "Homepage concept",
    "service_page": "Service page concept",
    "call_flow": "AI call flow concept",
    "portal": "Client portal concept",
}


def qualified(session: Session, conversation: Conversation, *, transmitting: bool = False) -> bool:
    """Require human qualification, a real original thread and no unknown transmission."""
    card = session.scalar(select(Opportunity).where(Opportunity.conversation_id == conversation.id))
    return bool(
        card
        and card.stage in QUALIFIED
        and conversation.first_message_id
        and conversation.state not in {"stopped", "held"}
        and not session.scalar(
            select(SendIntent.id)
            .where(
                SendIntent.conversation_id == conversation.id,
                SendIntent.state.in_(
                    ["uncertain"] if transmitting else ["uncertain", "transmitting"]
                ),
            )
            .limit(1)
        )
    )


def _owner(session: Session, actor_id: str) -> None:
    actor = session.get(User, actor_id)
    if not actor or not actor.active or actor.role != "owner":
        raise ValueError("Only the owner can create or publish concepts")


def create_concept(
    db: Database,
    company_id: str,
    kind: str,
    headline: str,
    introduction: str,
    steps: list[str],
    evidence_ids: list[str],
    actor_id: str,
) -> DemoVersion:
    """Create an unapproved concept from the owner's brief; never invent client results."""
    if (
        kind not in KINDS
        or not 5 <= len(headline.strip()) <= 160
        or not 20 <= len(introduction.strip()) <= 1500
        or not 2 <= len(steps) <= 6
        or any(not 5 <= len(step.strip()) <= 300 for step in steps)
    ):
        raise ValueError("Choose a concept type, clear heading, introduction and 2–6 short steps")
    if not 1 <= len(evidence_ids) <= 10:
        raise ValueError("Reference 1–10 recorded business observations")
    try:
        with db.sessions.begin() as session:
            _owner(session, actor_id)
            company = session.get(Company, company_id)
            conversation = session.scalar(
                select(Conversation).where(Conversation.company_id == company_id)
            )
            if not company or not conversation or not qualified(session, conversation):
                raise ValueError(
                    "Concepts require a human-qualified, confirmed existing conversation"
                )
            evidence = list(
                session.scalars(
                    select(Evidence).where(
                        Evidence.company_id == company_id, Evidence.id.in_(evidence_ids)
                    )
                )
            )
            if {item.id for item in evidence} != set(evidence_ids):
                raise ValueError("Use evidence from this business only")
            version = (
                session.scalar(
                    select(func.max(DemoVersion.version)).where(
                        DemoVersion.company_id == company_id, DemoVersion.kind == kind
                    )
                )
                or 0
            ) + 1
            snapshot: dict[str, Any] = {
                "company_id": company_id,
                "company_name": company.name,
                "kind": kind,
                "headline": headline.strip(),
                "introduction": introduction.strip(),
                "steps": [value.strip() for value in steps],
                "evidence_ids": sorted(evidence_ids),
                "version": version,
                "label": "Concept only — not a working client implementation",
            }
            demo = DemoVersion(
                company_id=company_id,
                conversation_id=conversation.id,
                kind=kind,
                version=version,
                snapshot=snapshot,
                digest=fingerprint(snapshot),
                created_by=actor_id,
            )
            session.add(demo)
            session.flush()
            session.add(AuditEvent(actor_id=actor_id, action="concept.created", entity_id=demo.id))
            return demo
    except IntegrityError as error:
        raise ValueError(
            "A concurrent concept revision was saved; reload before editing"
        ) from error


def publish_concept(
    db: Database, settings: Settings, demo_id: str, digest: str, actor_id: str
) -> None:
    """Publish exactly one reviewed revision under an unguessable read-only share link."""
    if not settings.public_base_url:
        raise ValueError(
            "Configure the deployed PUBLIC_BASE_URL before creating prospect share links"
        )
    with db.sessions.begin() as session:
        _owner(session, actor_id)
        demo = session.get(DemoVersion, demo_id)
        conversation = session.get(Conversation, demo.conversation_id) if demo else None
        if (
            not demo
            or demo.state != "draft"
            or demo.digest != digest
            or fingerprint(demo.snapshot) != digest
            or not conversation
            or not qualified(session, conversation)
        ):
            raise ValueError("Review a current concept for a qualified conversation before sharing")
        token = secrets.token_urlsafe(32)
        changed = session.scalar(
            update(DemoVersion)
            .where(
                DemoVersion.id == demo_id,
                DemoVersion.state == "draft",
                DemoVersion.digest == digest,
            )
            .values(
                state="approved",
                share_token=token,
                share_url=settings.public_base_url.rstrip("/") + "/concepts/" + token,
                approved_by=actor_id,
                approved_at=utc_now(),
            )
            .returning(DemoVersion.id)
        )
        if not changed:
            raise ValueError("The concept changed while approval was recorded")
        session.add(AuditEvent(actor_id=actor_id, action="concept.published", entity_id=demo_id))


def revoke_concept(db: Database, demo_id: str, actor_id: str) -> None:
    """Revoke a public concept link; unsent drafts referencing it fail their next policy check."""
    with db.sessions.begin() as session:
        _owner(session, actor_id)
        demo = session.get(DemoVersion, demo_id)
        if not demo:
            raise ValueError("Concept not found")
        demo.state = "revoked"
        session.add(AuditEvent(actor_id=actor_id, action="concept.revoked", entity_id=demo_id))


def concept_attachment(
    session: Session, demo_id: str, conversation: Conversation, *, transmitting: bool = False
) -> dict[str, str]:
    """Bind a message to the exact approved concept revision, content digest and share URL."""
    demo = session.get(DemoVersion, demo_id)
    if (
        not demo
        or demo.conversation_id != conversation.id
        or demo.state != "approved"
        or not demo.share_url
        or fingerprint(demo.snapshot) != demo.digest
        or not qualified(session, conversation, transmitting=transmitting)
    ):
        raise ValueError("Use an approved, unchanged concept for this qualified conversation")
    return {"kind": "concept_link", "id": demo.id, "digest": demo.digest, "url": demo.share_url}


def demo_message(db: Database, demo_id: str, body: str, actor_id: str) -> Draft:
    """Create one owner-requested unapproved same-thread message for an approved concept."""
    from app.drafting.service import save_draft

    with db.sessions() as session:
        _owner(session, actor_id)
        demo = session.get(DemoVersion, demo_id)
        if not demo:
            raise ValueError("Concept not found")
        conversation = session.get(Conversation, demo.conversation_id)
        assert conversation
        attachment = concept_attachment(session, demo_id, conversation)
        for existing in session.scalars(
            select(Draft)
            .where(
                Draft.conversation_id == conversation.id,
                Draft.kind == "concept",
                Draft.state != "superseded",
            )
            .order_by(Draft.created_at.desc())
        ):
            if any(item.get("id") == demo.id for item in existing.snapshot.get("attachments", [])):
                return existing
        initial = session.scalar(
            select(Draft).where(
                Draft.conversation_id == conversation.id, Draft.step == 0, Draft.state == "sent"
            )
        )
        if not initial:
            raise ValueError("A confirmed initial message is required")
        step = max(
            2,
            (
                session.scalar(
                    select(func.max(Draft.step)).where(Draft.conversation_id == conversation.id)
                )
                or 0
            )
            + 1,
        )
        if step > 20:
            raise ValueError("This conversation needs a manual mailbox review before more concepts")
        if attachment["url"] not in body:
            raise ValueError("Include this exact approved concept link in the message body")
    return save_draft(
        db,
        demo.company_id,
        conversation.campaign_id,
        conversation.contact_id,
        conversation.mailbox_id,
        initial.snapshot["subject"],
        body,
        demo.snapshot["evidence_ids"],
        actor_id,
        step=step,
        concept_id=demo.id,
    )


def record_demo_acceptance(session: Session, draft: Draft) -> None:
    """Advance only a still-qualified early sales card after actual SMTP/Sent acceptance."""
    if not draft.snapshot.get("attachments"):
        return
    card = session.scalar(
        select(Opportunity).where(Opportunity.conversation_id == draft.conversation_id)
    )
    if card and card.stage == "Interested":
        card.stage, card.version = "Demo sent", card.version + 1
        card.updated_at = utc_now()
        session.add(
            PipelineEvent(
                opportunity_id=card.id,
                stage="Demo sent",
                note="Exact approved concept message accepted by the sender provider.",
            )
        )
