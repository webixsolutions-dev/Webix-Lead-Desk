"""Draft revision and approval rules, separate from any delivery transport."""

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.db.lead_models import Campaign, CampaignLead, Company, CompanyDomain, Contact, ContactBasis
from app.db.models import AuditEvent, User
from app.db.outreach_models import Conversation, Draft, DraftApproval, Mailbox
from app.db.research_models import Evidence
from app.db.session import Database
from app.drafting.content import check_copy, fingerprint, footer
from app.playbook.service import current_guidance
from app.policy.contacts import is_suppressed
from app.sources.validation import domain_of


def save_draft(
    db: Database,
    company_id: str,
    campaign_id: str,
    contact_id: str,
    mailbox_id: str,
    subject: str,
    body: str,
    evidence_ids: list[str],
    actor_id: str,
    *,
    previous_id: str | None = None,
    step: int = 0,
) -> Draft:
    """Create a revision; supersede the expected predecessor atomically without reusing approval."""
    check_copy(subject, body, first_touch=step == 0)
    if step not in {0, 1} or not 1 <= len(evidence_ids) <= 10:
        raise ValueError("Use an initial message or one follow-up, with 1–10 evidence references")
    guidance = current_guidance(db, campaign_id)
    try:
        with db.sessions.begin() as session:
            campaign = session.get(Campaign, campaign_id)
            contact = session.get(Contact, contact_id)
            mailbox = session.get(Mailbox, mailbox_id)
            member = session.scalar(
                select(CampaignLead.id).where(
                    CampaignLead.company_id == company_id, CampaignLead.campaign_id == campaign_id
                )
            )
            if (
                not campaign
                or not campaign.active
                or not contact
                or contact.company_id != company_id
                or not mailbox
                or mailbox.lane != campaign.lane
                or not member
            ):
                raise ValueError("Choose a contact and sender matching this business and campaign")
            found = set(
                session.scalars(
                    select(Evidence.id).where(
                        Evidence.company_id == company_id, Evidence.id.in_(evidence_ids)
                    )
                )
            )
            if found != set(evidence_ids):
                raise ValueError("Use evidence recorded for this business")
            conversation = session.scalar(
                select(Conversation).where(Conversation.company_id == company_id)
            )
            if conversation is None:
                conversation = Conversation(
                    company_id=company_id,
                    campaign_id=campaign_id,
                    contact_id=contact_id,
                    mailbox_id=mailbox_id,
                )
                session.add(conversation)
                session.flush()
            elif (conversation.campaign_id, conversation.contact_id, conversation.mailbox_id) != (
                campaign_id,
                contact_id,
                mailbox_id,
            ):
                if conversation.locked_at or conversation.state != "draft" or step:
                    raise ValueError("This business is locked to its existing contact and sender")
                conversation.campaign_id, conversation.contact_id, conversation.mailbox_id = (
                    campaign_id,
                    contact_id,
                    mailbox_id,
                )
            if conversation.state in {"replied", "held", "stopped"}:
                raise ValueError("This conversation is stopped or requires a human response")
            if step and not conversation.first_message_id:
                raise ValueError("A follow-up requires an accepted first message")
            previous = session.scalar(
                select(Draft)
                .where(Draft.conversation_id == conversation.id, Draft.step == step)
                .order_by(Draft.revision.desc())
                .limit(1)
            )
            if previous:
                if previous.id != previous_id or previous.state not in {
                    "draft",
                    "approved",
                    "rejected",
                }:
                    raise ValueError("Draft changed or was dispatched. Reload before editing.")
                changed = session.scalar(
                    update(Draft)
                    .where(Draft.id == previous.id, Draft.state == previous.state)
                    .values(state="superseded")
                    .returning(Draft.id)
                )
                if not changed:
                    raise ValueError("Another editor changed this draft")
            elif previous_id:
                raise ValueError("Previous draft not found")
            snapshot: dict[str, Any] = {
                "company_id": company_id,
                "campaign_id": campaign_id,
                "contact_id": contact_id,
                "mailbox_id": mailbox_id,
                "from_email": mailbox.email,
                "from_name": mailbox.sender_name,
                "recipient": contact.email,
                "subject": subject.strip(),
                "body": body.strip(),
                "footer": footer(mailbox),
                "evidence_ids": sorted(found),
                "playbook_ids": [item.id for item in guidance],
                "attachments": [],
                "step": step,
                "conversation_id": conversation.id,
                "in_reply_to": conversation.last_message_id if step else None,
                "references": [conversation.first_message_id] if step else [],
            }
            item = Draft(
                conversation_id=conversation.id,
                step=step,
                revision=previous.revision + 1 if previous else 1,
                snapshot=snapshot,
                digest=fingerprint(snapshot),
                created_by=actor_id,
            )
            session.add(item)
            session.flush()
            session.add(AuditEvent(actor_id=actor_id, action="draft.created", entity_id=item.id))
            return item
    except IntegrityError as error:
        raise ValueError(
            "A concurrent draft changed this conversation. Reload before editing."
        ) from error


def approval_blocks(db: Database, draft: Draft) -> list[str]:
    """Recheck identity, contact evidence, suppression and current sender against the snapshot."""
    snapshot = draft.snapshot
    reasons: list[str] = []
    if fingerprint(snapshot) != draft.digest:
        reasons.append("Content fingerprint changed")
    with db.sessions() as session:
        conversation = session.get(Conversation, draft.conversation_id)
        company = session.get(Company, snapshot["company_id"])
        contact = session.get(Contact, snapshot["contact_id"])
        mailbox = session.get(Mailbox, snapshot["mailbox_id"])
        campaign = session.get(Campaign, snapshot["campaign_id"])
        if not conversation or conversation.state in {"held", "stopped", "replied"}:
            reasons.append("Conversation is held or has a reply")
        if not campaign or not campaign.active:
            reasons.append("Campaign is inactive")
        if conversation and (
            conversation.contact_id != snapshot["contact_id"]
            or conversation.mailbox_id != snapshot["mailbox_id"]
            or conversation.campaign_id != snapshot["campaign_id"]
        ):
            reasons.append("Conversation routing changed")
        if campaign and mailbox and campaign.lane != mailbox.lane:
            reasons.append("Sender lane does not match the campaign")
        if (
            not mailbox
            or mailbox.provider != "titan"
            or (
                mailbox.email != snapshot["from_email"]
                or footer(mailbox) != snapshot["footer"]
                or mailbox.sender_name != snapshot["from_name"]
            )
        ):
            reasons.append("Sender identity changed or is not an eligible Titan profile")
        if (
            not company
            or not company.website
            or not session.scalar(
                select(CompanyDomain.id).where(
                    CompanyDomain.company_id == company.id,
                    CompanyDomain.domain == domain_of(company.website),
                    CompanyDomain.status == "verified",
                )
            )
        ):
            reasons.append("Website identity needs review")
        if (
            not contact
            or contact.email != snapshot["recipient"]
            or contact.verification != "verified"
            or not contact.verified_at
            or contact.verified_at < utc_now() - timedelta(days=30)
        ):
            reasons.append("Contact address needs recent verification")
        basis = session.scalar(
            select(ContactBasis)
            .where(ContactBasis.contact_id == snapshot["contact_id"])
            .order_by(ContactBasis.observed_at.desc())
            .limit(1)
        )
        if not basis or basis.no_solicitation or basis.expires_at <= utc_now():
            reasons.append("Contact permission evidence is missing, restricted or expired")
        if company and is_suppressed(db, snapshot["recipient"], company.website):
            reasons.append("Global suppression prevents outreach")
    return reasons


def approve(db: Database, draft_id: str, digest: str, actor_id: str) -> None:
    """Approve only the exact displayed draft after current eligibility checks."""
    with db.sessions() as session:
        actor = session.get(User, actor_id)
        item = session.get(Draft, draft_id)
        if not actor or not actor.active or actor.role != "owner":
            raise ValueError("Only the owner can approve a message")
        if item is None or item.state != "draft" or digest != item.digest:
            raise ValueError("Draft changed. Reload before approving.")
    reasons = approval_blocks(db, item)
    if reasons:
        raise ValueError("; ".join(reasons))
    with db.sessions.begin() as session:
        changed = session.scalar(
            update(Draft)
            .where(Draft.id == draft_id, Draft.state == "draft", Draft.digest == digest)
            .values(state="approved")
            .returning(Draft.id)
        )
        if not changed:
            raise ValueError("Draft changed while approval was being recorded")
        session.add(DraftApproval(draft_id=draft_id, digest=digest, approved_by=actor_id))
        session.add(AuditEvent(actor_id=actor_id, action="draft.approved", entity_id=draft_id))
