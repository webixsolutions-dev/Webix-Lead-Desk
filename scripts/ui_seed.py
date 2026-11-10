"""Synthetic lead for browser review fixtures; no external provider actions."""

from datetime import timedelta

from app.core.clock import utc_now
from app.db.lead_models import Campaign, CampaignLead, Company, Contact, ContactBasis
from app.db.outreach_models import Mailbox
from app.db.research_models import Evidence
from app.db.session import Database
from app.drafting.service import save_draft


def seed_review(db: Database, actor_id: str) -> tuple[str, str]:
    """Create a fictional draft through the application service and return its local paths."""
    with db.sessions.begin() as session:
        company = Company(
            identity_key="synthetic-ui-heating",
            name="Example Heating Studio",
            city="Calgary",
            industry="HVAC",
            website="https://example.com",
        )
        campaign = Campaign(
            name="Example Calgary campaign",
            area="Calgary, Alberta",
            industry="HVAC",
            services=["Website redesign"],
            active=True,
        )
        session.add_all([company, campaign])
        session.flush()
        contact = Contact(
            company_id=company.id,
            email="service@example.com",
            verification="verified",
            verified_at=utc_now(),
            allow_role=True,
        )
        mailbox = Mailbox(
            email="sales@example.org",
            sender_name="Demo Owner",
            brand="Example Studio",
            lane="client",
            mailing_address="1 Example Street, Calgary",
            contact_details="sales@example.org",
        )
        evidence = Evidence(
            company_id=company.id,
            kind="business",
            source_url="https://example.com/services",
            facts={"note": "Heating estimates listed."},
            reviewed_by=actor_id,
        )
        session.add_all(
            [
                contact,
                mailbox,
                evidence,
                CampaignLead(company_id=company.id, campaign_id=campaign.id),
            ]
        )
        session.flush()
        session.add(
            ContactBasis(
                contact_id=contact.id,
                kind="publication",
                source_url="https://example.com/contact",
                relevance="Heating quote enquiries",
                reviewed_by=actor_id,
                observed_at=utc_now(),
                expires_at=utc_now() + timedelta(days=30),
            )
        )
    draft = save_draft(
        db,
        company.id,
        campaign.id,
        contact.id,
        mailbox.id,
        "A question about heating estimates",
        "Your service page lists heating estimates. "
        "How does your team currently handle quote requests?",
        [evidence.id],
        actor_id,
    )
    return f"/leads/{company.id}/draft?step=0&kind=first_touch", f"/drafts/{draft.id}/approve"
