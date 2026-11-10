"""Separate partner capacity, targeting history and manual channel controls."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.form_models import FormDraft
from app.db.lead_models import Campaign, ChannelReservation, Company
from app.db.outreach_models import Mailbox
from app.db.partner_models import PartnerDay, PartnerReview, PartnerSlot
from app.db.pipeline_models import Opportunity
from app.db.session import Database
from app.drafting.service import approval_blocks
from app.partners.forms import approve_form, form_block, record_form, save_form
from app.partners.service import reserve_slot, review_partner
from app.pipeline.service import update_opportunity
from app.policy.contacts import reserve_channel, suppress
from app.reporting.service import campaign_results
from app.sending.window import CALGARY
from app.sources.campaigns import edit_campaign
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_pipeline_demos import card_values, replied_card
from tests.test_research import research_company as research_company


def partner_mode(db: Database, setup: dict[str, str]) -> None:
    """Give the synthetic fixture its distinct partner campaign and sender lane."""
    with db.sessions.begin() as session:
        campaign = session.get(Campaign, setup["campaign_id"])
        mailbox = session.get(Mailbox, setup["mailbox_id"])
        assert campaign and mailbox
        campaign.lane = "partner"
        campaign.daily_limit = 5
        mailbox.lane = "partner"
        mailbox.provider = "hostinger"
        mailbox.brand = "Webix Solutions"


def review(db: Database, company: str, actor: str, country: str = "CA") -> None:
    """Record specific synthetic jurisdiction evidence without any external approval claim."""
    review_partner(
        db,
        company,
        country,
        "https://example.com/about",
        "Synthetic Canadian business; reviewed sender and recipient permission rules.",
        actor,
    )


def test_partner_review_is_required_and_rechecked(
    db: Database, drafting_setup: dict[str, str], reviewer: Account
) -> None:
    """Drafting and approval require a current country review from an active owner."""
    partner_mode(db, drafting_setup)
    with pytest.raises(ValueError, match="country"):
        make_draft(db, drafting_setup)
    with pytest.raises(ValueError, match="owner"):
        review(db, drafting_setup["company_id"], reviewer.user.id)
    review(db, drafting_setup["company_id"], drafting_setup["actor_id"])
    draft = make_draft(db, drafting_setup)
    assert not approval_blocks(db, draft)
    with db.sessions.begin() as session:
        recorded = session.get(PartnerReview, drafting_setup["company_id"])
        assert recorded
        recorded.reviewed_at = utc_now() - timedelta(days=31)
    assert any("country" in block for block in approval_blocks(db, draft))


def test_partner_global_cap_is_atomic_and_revisions_do_not_spend_again(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Concurrent new businesses across campaigns cannot exceed five reservations."""
    partner_mode(db, drafting_setup)
    actor = drafting_setup["actor_id"]
    companies = []
    with db.sessions.begin() as session:
        other = Campaign(
            name="Another partner campaign",
            industry="Agency",
            area="Canada",
            services=["Web design"],
            lane="partner",
            daily_limit=5,
        )
        session.add(other)
        session.flush()
        other_id = other.id
        for i in range(8):
            company = Company(
                identity_key=f"partner-{i}", name=f"Partner {i}", city="Calgary", industry="Agency"
            )
            session.add(company)
            session.flush()
            companies.append(company.id)
    for company_id in companies:
        review(db, company_id, actor)

    def claim(index: int) -> bool:
        """Race a slot through the same transaction used by drafting and AI preflight."""
        try:
            with db.sessions.begin() as session:
                campaign = session.get(
                    Campaign, other_id if index % 2 else drafting_setup["campaign_id"]
                )
                assert campaign
                reserve_slot(session, companies[index], campaign, utc_now())
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(8))) == 5
    with db.sessions.begin() as session:
        slot = session.scalar(select(PartnerSlot))
        assert slot
        campaign = session.get(Campaign, other_id)
        assert campaign
        reserve_slot(session, slot.company_id, campaign, utc_now())
        day = session.get(PartnerDay, utc_now().astimezone(CALGARY).date().isoformat())
        assert day and day.reserved == 5


def test_targeting_edits_keep_lane_and_require_version(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Targeting can change without silently moving conversation or sender history."""
    values: dict[str, Any] = dict(
        name="Agency partners",
        industry="Agencies",
        area="Toronto",
        country="CA",
        services=["Portals"],
        daily_limit=3,
        active=True,
    )
    edit_campaign(db, drafting_setup["campaign_id"], 1, drafting_setup["actor_id"], **values)
    with pytest.raises(ValueError, match="another tab"):
        edit_campaign(db, drafting_setup["campaign_id"], 1, drafting_setup["actor_id"], **values)
    with db.sessions() as session:
        campaign = session.get(Campaign, drafting_setup["campaign_id"])
        assert campaign
        assert (
            campaign.version == 2 and campaign.industry == "Agencies" and campaign.lane == "client"
        )


def form_copy(db: Database, setup: dict[str, str], version: int = 0) -> FormDraft:
    """Prepare specific manual first-touch copy using existing reviewed website evidence."""
    return save_form(
        db,
        setup["company_id"],
        setup["campaign_id"],
        setup["mailbox_id"],
        "https://example.com/contact",
        "I noticed your heating estimates. Would a short quote-page suggestion be useful?",
        "Reviewed the form purpose and enquiry relevance; no restrictions were present.",
        version,
        setup["actor_id"],
    )


def test_form_revisions_exact_approval_and_single_channel(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Edits invalidate the previous copy and an approved form prevents email first touch."""
    first = form_copy(db, drafting_setup)
    latest = form_copy(db, drafting_setup, 1)
    with pytest.raises(ValueError, match="current exact"):
        approve_form(db, first.id, first.digest, drafting_setup["actor_id"])
    with pytest.raises(ValueError, match="Only one outcome"):
        record_form(
            db, latest.id, "submitted", "Synthetic manual confirmation", drafting_setup["actor_id"]
        )
    approve_form(db, latest.id, latest.digest, drafting_setup["actor_id"])
    assert not reserve_channel(db, drafting_setup["company_id"], "email")
    suppress(db, "service@example.com", "Synthetic stop after approval")
    assert "suppression" in str(form_block(db, latest))
    record_form(
        db,
        latest.id,
        "uncertain",
        "No success confirmation was visible; will not repeat.",
        drafting_setup["actor_id"],
    )
    with pytest.raises(ValueError, match="already approved"):
        form_copy(db, drafting_setup, 2)
    with db.sessions() as session:
        channel = session.get(ChannelReservation, drafting_setup["company_id"])
        assert channel and channel.channel == "form"


def test_form_global_stop_and_email_reservation_block_other_channel(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """A contact form cannot be used to evade a recipient stop or an existing email reservation."""
    item = form_copy(db, drafting_setup)
    assert reserve_channel(db, drafting_setup["company_id"], "email")
    with pytest.raises(ValueError, match="Email is already"):
        approve_form(db, item.id, item.digest, drafting_setup["actor_id"])
    suppress(db, "service@example.com", "Synthetic opt out")
    with pytest.raises(ValueError, match="suppression"):
        form_copy(db, drafting_setup, 1)


def test_reports_count_real_acceptances_and_reviewed_outcomes(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Repeated joins do not multiply send, reply or recorded deal values."""
    card = replied_card(db, delivery_setup, drafting_setup)
    update_opportunity(
        db,
        card.id,
        card.version,
        drafting_setup["actor_id"],
        **card_values(drafting_setup["actor_id"]),
    )
    row = campaign_results(db)[0]
    assert (
        row["first_sent"] == 1 and row["replied"] == 1 and row["qualified"] == 1 and row["won"] == 0
    )
    with db.sessions.begin() as session:
        saved = session.get(Opportunity, card.id)
        assert saved
        saved.stage = "Won"
        saved.setup_cad_cents = 120000
        saved.recurring_cad_cents = 20000
    row = campaign_results(db)[0]
    assert row["won"] == 1 and row["setup_cad"] == 120000 and row["monthly_cad"] == 20000


def test_partner_and_form_routes_require_owner_review(
    client: TestClient, owner: Account, db: Database, drafting_setup: dict[str, str]
) -> None:
    """The actual dashboard exposes targeting, copy review and explicit manual action labels."""
    sign_in(client, owner)
    page = client.get("/leads/results")
    assert page.status_code == 200 and "cash collected" in page.text
    page = client.get(f"/leads/{drafting_setup['company_id']}/form")
    assert page.status_code == 200
    response = client.post(
        f"/partners/{drafting_setup['company_id']}/review",
        data={
            "csrf_token": csrf_from(page.text),
            "country": "CA",
            "source_url": "https://example.com",
            "note": "A specific review without the confirmation",
        },
    )
    assert response.status_code == 422
    item = form_copy(db, drafting_setup)
    response = client.post(
        f"/forms/{item.id}/review",
        data={
            "csrf_token": csrf_from(page.text),
            "reviewed": "true",
            "action": "approve",
            "digest": item.digest,
        },
    )
    assert response.status_code == 303
    assert "Open approved form" in client.get(f"/leads/{drafting_setup['company_id']}/form").text
