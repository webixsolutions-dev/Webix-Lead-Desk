"""Draft revision and approval rules, separate from any delivery transport."""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.db.lead_models import Campaign, CampaignLead, Company, CompanyDomain, Contact, ContactBasis
from app.db.models import AuditEvent, User
from app.db.outreach_models import Conversation, Draft, DraftApproval, Mailbox
from app.db.research_models import Evidence
from app.db.session import Database
from app.demos.service import concept_attachment
from app.drafting.content import check_copy, fingerprint, footer, links
from app.learning.capture import capture_edit
from app.linkedin.coordination import outreach_block
from app.linkedin.response_policy import requested_response
from app.partners.service import partner_block, reserve_slot
from app.playbook.partner import meeting_block
from app.playbook.service import current_guidance
from app.policy.contacts import company_suppressed, is_suppressed
from app.policy.verification import verification_block, verify_address
from app.sending.sequences import days_for
from app.sending.signatures import get_signature
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
    concept_id: str | None = None,
    kind: str | None = None,
    allow_price: bool = False,
) -> Draft:
    """Create a revision; supersede the expected predecessor atomically without reusing approval."""
    if allow_price and kind != "reply":
        raise ValueError("Prices are permitted only in an owner-reviewed reply")
    check_copy(subject, body, first_touch=step == 0, allow_price=allow_price)
    if not 0 <= step <= 20 or not 1 <= len(evidence_ids) <= 10:
        raise ValueError("Use a supported message step with 1–10 evidence references")
    guidance = current_guidance(db, campaign_id)
    if previous_id and not concept_id:
        with db.sessions() as session:
            old_draft = session.get(Draft, previous_id)
            if old_draft and old_draft.snapshot.get("attachments"):
                concept_id = old_draft.snapshot["attachments"][0]["id"]
    kind = kind or ("concept" if concept_id else "follow_up" if step else "first_touch")
    if kind not in {"first_touch", "follow_up", "concept", "reply"}:
        raise ValueError("Invalid message kind")
    if (kind == "concept") != bool(concept_id) or (kind == "first_touch") != (step == 0):
        raise ValueError("Message kind does not match its sequence or concept")
    if step > 4 and kind == "follow_up":
        raise ValueError("Only four follow-ups are supported")
    sequence_step = step if kind == "follow_up" else 0
    try:
        with db.sessions.begin() as session:
            if allow_price:
                actor = session.get(User, actor_id)
                if not actor or not actor.active or actor.role != "owner":
                    raise ValueError("Only the owner can include a quotation")
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
            company = session.get(Company, company_id)
            assert company
            if reason := meeting_block(session, company, campaign, body):
                raise ValueError(reason)
            if campaign.lane == "partner" and (
                mailbox.provider != "hostinger" or mailbox.brand.casefold() != "webix solutions"
            ):
                raise ValueError(
                    "Partner outreach requires the Webix Solutions Hostinger business identity"
                )
            if kind == "follow_up" and step > len(days_for(campaign)):
                raise ValueError("Follow-up is not enabled for this campaign")
            if kind == "reply" and (
                not session.scalar(
                    select(Conversation.id).where(
                        Conversation.company_id == company_id, Conversation.state == "replied"
                    )
                )
            ):
                raise ValueError("A manual reply requires a recipient reply")
            if step == 0:
                reserve_slot(session, company_id, campaign, utc_now())
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
            if conversation.state in {"held", "stopped"} or (
                conversation.state == "replied" and kind not in {"concept", "reply"}
            ):
                raise ValueError("This conversation is stopped or requires a human response")
            if step and not conversation.first_message_id:
                raise ValueError("A follow-up requires an accepted first message")
            if step:
                initial = session.scalar(
                    select(Draft).where(
                        Draft.conversation_id == conversation.id,
                        Draft.step == 0,
                        Draft.state == "sent",
                    )
                )
                if not initial or subject.strip() != initial.snapshot["subject"]:
                    raise ValueError("Keep the original subject for this same-thread follow-up")
            previous = session.scalar(
                select(Draft)
                .where(
                    Draft.conversation_id == conversation.id,
                    Draft.kind == kind,
                    Draft.sequence_step == sequence_step
                    if kind == "follow_up"
                    else Draft.step == step,
                )
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
            # Delivery slots are monotonic and unique; sequence numbers belong to follow-ups.
            # A concept/reply can therefore never occupy a follow-up's sequence position.
            if kind == "follow_up":
                step = (
                    previous.step
                    if previous
                    else (
                        session.scalar(
                            select(func.max(Draft.step)).where(
                                Draft.conversation_id == conversation.id
                            )
                        )
                        or 0
                    )
                    + 1
                )
            attachments: list[dict[str, str]] = []
            if concept_id:
                actor = session.get(User, actor_id)
                if not actor or not actor.active or actor.role != "owner":
                    raise ValueError("Only the owner can prepare a concept response")
                if not concept_id and previous:
                    old = previous.snapshot.get("attachments", [])
                    concept_id = old[0].get("id") if old else None
                if not concept_id:
                    raise ValueError("Choose a reviewed concept before creating this response")
                attachment = concept_attachment(session, concept_id, conversation)
                if attachment["url"] not in body:
                    raise ValueError("Keep the exact approved concept link in the message")
                if set(links(body)) != {attachment["url"]}:
                    raise ValueError("Concept messages may include only the approved concept link")
                attachments = [attachment]
            if kind == "follow_up":
                earlier = session.scalars(
                    select(Draft).where(
                        Draft.conversation_id == conversation.id, Draft.state == "sent"
                    )
                )
                if any(
                    " ".join(d.snapshot["body"].lower().split()) == " ".join(body.lower().split())
                    for d in earlier
                ):
                    raise ValueError("Each follow-up needs its own angle, not the previous copy")
            snapshot: dict[str, Any] = {
                "company_id": company_id,
                "campaign_id": campaign_id,
                "contact_id": contact_id,
                "mailbox_id": mailbox_id,
                "from_email": mailbox.email,
                "provider": mailbox.provider,
                "from_name": mailbox.sender_name,
                "recipient": contact.email,
                "subject": subject.strip(),
                "allow_price": allow_price,
                "body": body.strip(),
                "footer": footer(mailbox),
                "signature": get_signature(session, mailbox_id),
                "evidence_ids": sorted(found),
                "playbook_ids": [item.id for item in guidance],
                "attachments": attachments,
                "step": step,
                "kind": kind,
                "angle": (
                    (
                        ["proof", "close the loop"]
                        if campaign.lane == "partner"
                        else ["problem", "proof", "different question or tip", "close the loop"]
                    )[sequence_step - 1]
                    if kind == "follow_up"
                    else kind
                ),
                "sequence_step": sequence_step,
                "conversation_id": conversation.id,
                "in_reply_to": conversation.last_message_id if step else None,
                "references": [conversation.first_message_id] if step else [],
            }
            item = Draft(
                conversation_id=conversation.id,
                step=step,
                kind=kind,
                sequence_step=sequence_step,
                revision=previous.revision + 1 if previous else 1,
                snapshot=snapshot,
                digest=fingerprint(snapshot),
                created_by=actor_id,
            )
            session.add(item)
            session.flush()
            session.add(AuditEvent(actor_id=actor_id, action="draft.created", entity_id=item.id))
            capture_edit(session, previous, item, actor_id)
            return item
    except IntegrityError as error:
        raise ValueError(
            "A concurrent draft changed this conversation. Reload before editing."
        ) from error


def approval_blocks(db: Database, draft: Draft) -> list[str]:
    """Recheck identity, contact evidence, suppression and current sender against the snapshot."""
    snapshot = draft.snapshot
    reasons: list[str] = []
    try:
        check_copy(
            snapshot["subject"],
            snapshot["body"],
            first_touch=draft.step == 0,
            allow_price=draft.kind == "reply" and snapshot.get("allow_price") is True,
        )
    except ValueError as error:
        reasons.append(str(error))
    if fingerprint(snapshot) != draft.digest:
        reasons.append("Content fingerprint changed")
    with db.sessions() as session:
        conversation = session.get(Conversation, draft.conversation_id)
        company = session.get(Company, snapshot["company_id"])
        if block := outreach_block(
            session, snapshot["company_id"], response=requested_response(session, draft)
        ):
            reasons.append(block)
        contact = session.get(Contact, snapshot["contact_id"])
        mailbox = session.get(Mailbox, snapshot["mailbox_id"])
        if get_signature(session, snapshot["mailbox_id"]) != snapshot.get(
            "signature", {"html": "", "text": "", "version": 0}
        ):
            reasons.append("Sender signature changed; save and review a new revision")
        campaign = session.get(Campaign, snapshot["campaign_id"])
        if (
            not conversation
            or conversation.state in {"held", "stopped"}
            or (conversation.state == "replied" and draft.kind not in {"concept", "reply"})
        ):
            reasons.append("Conversation is held or has a reply")
        if snapshot.get("attachments") and conversation:
            try:
                attachments = snapshot.get("attachments", [])
                if len(attachments) != 1:
                    raise ValueError("Concept binding missing")
                current = concept_attachment(
                    session,
                    attachments[0]["id"],
                    conversation,
                    transmitting=draft.state == "sending",
                )
                if current != attachments[0] or set(links(snapshot["body"])) != {current["url"]}:
                    raise ValueError("Concept binding changed")
            except (ValueError, KeyError, TypeError):
                reasons.append("Concept approval, share link or qualification changed")
        if (
            company
            and campaign
            and (reason := meeting_block(session, company, campaign, snapshot["body"]))
        ):
            reasons.append(reason)
        if (
            campaign
            and campaign.lane == "partner"
            and mailbox
            and (mailbox.provider != "hostinger" or mailbox.brand.casefold() != "webix solutions")
        ):
            reasons.append("Partner sender must be Webix Solutions on Hostinger")
        if not campaign or not campaign.active:
            reasons.append("Campaign is inactive")
        elif block := partner_block(session, snapshot["company_id"], campaign):
            reasons.append(block)
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
            or mailbox.provider not in {"titan", "hostinger", "gmail"}
            or (
                mailbox.provider != snapshot.get("provider", "titan")
                or mailbox.email != snapshot["from_email"]
                or footer(mailbox) != snapshot["footer"]
                or mailbox.sender_name != snapshot["from_name"]
            )
        ):
            reasons.append("Sender identity changed or is not an eligible sender profile")
        if (
            not company
            or (
                company.has_website != "false"
                and (
                    not company.website
                    or not session.scalar(
                        select(CompanyDomain.id).where(
                            CompanyDomain.company_id == company.id,
                            CompanyDomain.domain == domain_of(company.website),
                            CompanyDomain.status == "verified",
                        )
                    )
                )
            )
            or (
                company.has_website == "false"
                and not session.scalar(
                    select(Evidence.id).where(
                        Evidence.company_id == company.id,
                        Evidence.kind == "listing",
                        Evidence.reviewed_by.is_not(None),
                    )
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
        if contact and (reason := verification_block(contact)):
            reasons.append(reason)
        basis = session.scalar(
            select(ContactBasis)
            .where(ContactBasis.contact_id == snapshot["contact_id"])
            .order_by(ContactBasis.observed_at.desc())
            .limit(1)
        )
        if not basis or basis.no_solicitation or basis.expires_at <= utc_now():
            reasons.append("Contact permission evidence is missing, restricted or expired")
        if company and (
            is_suppressed(db, snapshot["recipient"], company.website)
            or company_suppressed(db, company.id)
        ):
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
    verify_address(db, item.snapshot["contact_id"])
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
