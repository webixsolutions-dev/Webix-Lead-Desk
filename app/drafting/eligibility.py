"""Current eligibility checks shared by review, scheduling and transmission."""

from datetime import timedelta

from sqlalchemy import select

from app.core.clock import utc_now
from app.db.lead_models import Campaign, Company, CompanyDomain, Contact, ContactBasis
from app.db.outreach_models import Conversation, Draft, Mailbox
from app.db.research_models import Evidence
from app.db.session import Database
from app.demos.service import concept_attachment
from app.drafting.check_context import email_report, stale_check
from app.drafting.content import check_copy, fingerprint, footer, links
from app.linkedin.coordination import outreach_block
from app.linkedin.response_policy import requested_response
from app.partners.service import partner_block
from app.playbook.partner import meeting_block
from app.policy.contacts import company_suppressed, is_suppressed
from app.policy.verification import verification_block
from app.sending.signatures import get_signature
from app.sources.validation import domain_of


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
        if snapshot.get("conversation_checks") and stale_check(
            snapshot["conversation_checks"], email_report(session, snapshot)
        ):
            reasons.append("Conversation or offer status changed; save and review a fresh revision")
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
