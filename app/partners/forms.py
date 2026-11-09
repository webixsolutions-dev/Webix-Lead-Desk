"""Prepare reviewed manual contact-form copy without external browser submissions."""

from collections.abc import Mapping

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.form_models import FormDraft
from app.db.lead_models import (
    Campaign,
    CampaignLead,
    ChannelReservation,
    Company,
    CompanyDomain,
    Contact,
    SuppressionEntry,
)
from app.db.models import AuditEvent, User
from app.db.outreach_models import Mailbox
from app.db.research_models import Evidence
from app.db.session import Database
from app.drafting.content import check_copy, fingerprint, footer
from app.linkedin.coordination import outreach_block, record_touch
from app.partners.service import partner_block, reserve_slot
from app.sources.validation import domain_of, website_url


def _owner(session: Session, actor_id: str) -> None:
    actor = session.get(User, actor_id)
    if not actor or not actor.active or actor.role != "owner":
        raise ValueError("Only the owner can review contact-form outreach")


def _eligible(session: Session, snapshot: Mapping[str, object]) -> None:
    company = session.get(Company, str(snapshot["company_id"]))
    campaign = session.get(Campaign, str(snapshot["campaign_id"]))
    mailbox = session.get(Mailbox, str(snapshot["mailbox_id"]))
    if (
        not company
        or not campaign
        or not campaign.active
        or not mailbox
        or mailbox.lane != campaign.lane
    ):
        raise ValueError("Choose an active campaign and its separate sender profile")
    if mailbox.provider != "titan" or mailbox.email.rsplit("@", 1)[-1] in {
        "gmail.com",
        "googlemail.com",
    }:
        raise ValueError("Use the reviewed business sender profile")
    if footer(mailbox) != snapshot["footer"] or mailbox.email != snapshot["from_email"]:
        raise ValueError("Sender identity changed; prepare a new form revision")
    if reason := outreach_block(session, company.id):
        raise ValueError(reason)
    domain = domain_of(company.website) if company.website else ""
    if (
        not domain
        or domain_of(str(snapshot["form_url"])) != domain
        or not session.scalar(
            select(CompanyDomain.id).where(
                CompanyDomain.company_id == company.id,
                CompanyDomain.domain == domain,
                CompanyDomain.status == "verified",
            )
        )
    ):
        raise ValueError("Use a contact form on the verified business website")
    keys = {"domain:" + domain}
    for email in session.scalars(select(Contact.email).where(Contact.company_id == company.id)):
        keys.update({"email:" + email, "domain:" + email.rsplit("@", 1)[-1]})
    if session.scalar(select(SuppressionEntry.id).where(SuppressionEntry.key.in_(keys)).limit(1)):
        raise ValueError("Global suppression blocks this business on every channel")
    channel = session.get(ChannelReservation, company.id)
    if channel and channel.channel != "form":
        raise ValueError("Email is already reserved for this business; do not also submit a form")
    if reason := partner_block(session, company.id, campaign):
        raise ValueError(reason)


def save_form(
    db: Database,
    company_id: str,
    campaign_id: str,
    mailbox_id: str,
    form_url: str,
    body: str,
    review_note: str,
    expected_version: int,
    actor_id: str,
) -> FormDraft:
    """Create a fresh unapproved revision; never change previously approved form copy."""
    check_copy("Business enquiry", body)
    url = website_url(form_url)
    if not 20 <= len(review_note.strip()) <= 2000:
        raise ValueError("Record relevance and why this form permits this business enquiry")
    try:
        with db.sessions.begin() as session:
            _owner(session, actor_id)
            mailbox = session.get(Mailbox, mailbox_id)
            campaign = session.get(Campaign, campaign_id)
            if (
                not mailbox
                or not campaign
                or not session.scalar(
                    select(CampaignLead.id).where(
                        CampaignLead.company_id == company_id,
                        CampaignLead.campaign_id == campaign_id,
                    )
                )
            ):
                raise ValueError("Choose a matching business, campaign and sender")
            evidence = session.scalar(
                select(Evidence.id).where(Evidence.company_id == company_id).limit(1)
            )
            if not evidence:
                raise ValueError("Research this business before preparing a form")
            previous = session.scalar(
                select(FormDraft)
                .where(FormDraft.company_id == company_id)
                .order_by(FormDraft.version.desc())
                .limit(1)
            )
            if (previous.version if previous else 0) != expected_version or (
                previous and previous.state != "draft"
            ):
                raise ValueError("Form changed or already approved; review the existing attempt")
            snapshot = {
                "company_id": company_id,
                "campaign_id": campaign_id,
                "mailbox_id": mailbox_id,
                "form_url": url,
                "body": body.strip(),
                "footer": footer(mailbox),
                "from_email": mailbox.email,
                "review_note": review_note.strip(),
            }
            _eligible(session, snapshot)
            reserve_slot(session, company_id, campaign, utc_now())
            if previous:
                previous.state = "superseded"
            item = FormDraft(
                company_id=company_id,
                campaign_id=campaign_id,
                version=expected_version + 1,
                snapshot=snapshot,
                digest=fingerprint(snapshot),
                created_by=actor_id,
            )
            session.add(item)
            session.flush()
            return item
    except IntegrityError as error:
        raise ValueError("A concurrent form revision changed; reload") from error


def approve_form(db: Database, form_id: str, digest: str, actor_id: str) -> None:
    """Approve exact manual copy and reserve the form channel before any external action."""
    try:
        with db.sessions.begin() as session:
            _owner(session, actor_id)
            item = session.get(FormDraft, form_id)
            if (
                not item
                or item.state != "draft"
                or item.digest != digest
                or fingerprint(item.snapshot) != digest
            ):
                raise ValueError("Review the current exact form revision")
            _eligible(session, item.snapshot)
            if not session.get(ChannelReservation, item.company_id):
                session.add(ChannelReservation(company_id=item.company_id, channel="form"))
                session.flush()
            changed = session.scalar(
                update(FormDraft)
                .where(
                    FormDraft.id == item.id, FormDraft.state == "draft", FormDraft.digest == digest
                )
                .values(state="approved", approved_by=actor_id, approved_at=utc_now())
                .returning(FormDraft.id)
            )
            if not changed:
                raise ValueError("Form changed during approval")
            session.add(AuditEvent(actor_id=actor_id, action="form.approved", entity_id=item.id))
    except IntegrityError as error:
        raise ValueError(
            "Another outreach channel was reserved; reload before continuing"
        ) from error


def record_form(db: Database, form_id: str, state: str, note: str, actor_id: str) -> None:
    """Record the owner's actual manual outcome; an uncertain result never offers a retry."""
    if state not in {"submitted", "uncertain"} or not 10 <= len(note.strip()) <= 1000:
        raise ValueError("Record the actual submission or uncertainty with a specific note")
    with db.sessions.begin() as session:
        _owner(session, actor_id)
        item = session.get(FormDraft, form_id)
        if not item or item.state != "approved":
            raise ValueError("Only one outcome may be recorded for an approved form")
        # Preserve truthful history even if an opt-out arrived after a manual submission.
        item.state, item.outcome_note, item.recorded_at = state, note.strip(), utc_now()
        session.add(AuditEvent(actor_id=actor_id, action="form." + state, entity_id=item.id))
        record_touch(
            session,
            item.company_id,
            "form:" + item.id,
            "form",
            "outbound",
            state,
            actor=actor_id,
            at=item.recorded_at,
        )


def form_block(db: Database, item: FormDraft) -> str | None:
    """Recheck current policy before showing a manual action link, without altering history."""
    try:
        with db.sessions() as session:
            if fingerprint(item.snapshot) != item.digest:
                return "Form content fingerprint changed"
            _eligible(session, item.snapshot)
    except ValueError as error:
        return str(error)
    return None
