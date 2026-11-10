"""Rule history, reviewed no-website facts, call consent and global stops."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.calls.board import call_list
from app.calls.listing import ListingReview, review_listing
from app.calls.service import record_call
from app.core.clock import utc_now
from app.db.acquisition_models import CallRecord, LeadEvent
from app.db.lead_models import Campaign, ChannelReservation, Contact, ContactBasis
from app.db.session import Database
from app.journey.project import derive, detail
from app.policy.contacts import company_suppressed
from app.scoring.service import Rules, save_rules, score
from tests.acquisition.helpers import business
from tests.conftest import Account, csrf_from, sign_in


def listing(*, mature: bool = False) -> ListingReview:
    """Explicit human observations; missing website is independently confirmed."""
    return ListingReview(
        website_status="false",
        review_count=15,
        rating=4.7,
        note="Owner confirmed they have no website; listing and recent posts checked.",
        source_url="https://example.com/listing",
        signals={"active_ads": True, "weak_mobile": True, "response_gap": True, "mature": mature},
    )


def test_rule_score_history_and_mutual_exclusion(db: Database, owner: Account) -> None:
    """Unknowns add zero, absent/mobile signals do not stack, and edits keep reasons."""
    company, campaign = business(db, owner)
    initial = score(db, company.id, campaign.id)
    assert initial.points == 2 and initial.band == "Skip"
    review_listing(db, company.id, listing(), owner.user.id)
    top = score(db, company.id, campaign.id)
    assert top.points == 10 and top.band == "Contact now"
    assert next(item for item in top.breakdown if item["signal"] == "weak_mobile")["points"] == 0
    assert score(db, company.id, campaign.id).id == top.id
    review_listing(db, company.id, listing(mature=True), owner.user.id)
    lower = score(db, company.id, campaign.id)
    assert lower.points == 10  # A strong site does not remove non-website opportunities.
    with db.sessions.begin() as session:
        saved_campaign = session.get(Campaign, campaign.id)
        assert saved_campaign
        saved_campaign.services = ["Websites and redesign"]
    save_rules(db, Rules(mature=-6), 0, owner.user.id)
    edited = score(db, company.id, campaign.id)
    assert edited.points == 4 and edited.band == "Later" and edited.policy_version == 1
    assert len(detail(db, company.id)["history"]) == 4


def test_call_consent_is_required_and_preserves_approval_gate(
    db: Database, owner: Account, reviewer: Account
) -> None:
    """A reviewer can capture explicit consent, never approve or send an email."""
    company, campaign = business(db, owner)
    _assign_member(db, owner, reviewer, company.id)
    review_listing(db, company.id, listing(), reviewer.user.id)
    assert len(call_list(db)) == 1
    with pytest.raises(ValueError, match="permission"):
        record_call(
            db,
            company.id,
            campaign.id,
            reviewer.user.id,
            "call-consent",
            "interested",
            "Asked for the homepage idea",
            email="hello@example.com",
        )
    item = record_call(
        db,
        company.id,
        campaign.id,
        reviewer.user.id,
        "call-consent",
        "interested",
        "Requested a homepage concept",
        email="hello@example.com",
        consent=True,
        consent_note=(
            "They explicitly asked us to email the discussed homepage concept to this address."
        ),
    )
    assert (
        record_call(
            db,
            company.id,
            campaign.id,
            reviewer.user.id,
            "call-consent",
            "interested",
            "Duplicate click",
        ).id
        == item.id
    )
    assert call_list(db) == []
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(CallRecord)) == 1
        basis = session.scalar(select(ContactBasis))
        assert basis and basis.kind == "express_consent" and basis.reviewed_by == reviewer.user.id
        channel = session.get(ChannelReservation, company.id)
        assert channel and channel.channel == "email"
        assert session.scalar(select(LeadEvent.id).where(LeadEvent.kind == "consent_recorded"))


def test_call_rejection_stops_future_new_email(db: Database, owner: Account) -> None:
    """A phone rejection applies even to an email discovered after the call."""
    company, campaign = business(db, owner)
    record_call(
        db,
        company.id,
        campaign.id,
        owner.user.id,
        "call-stop",
        "not_interested",
        "Asked us not to follow up",
    )
    with db.sessions.begin() as session:
        session.add(Contact(company_id=company.id, email="newlyfound@example.com"))
    assert company_suppressed(db, company.id)
    assert call_list(db) == []
    journey = detail(db, company.id)
    assert "Not interested" in journey["stopped_reason"]
    with pytest.raises(ValueError, match="suppressed"):
        record_call(
            db, company.id, campaign.id, owner.user.id, "second-call", "no_answer", "Tried again"
        )


def test_future_callback_and_meeting_dates(db: Database, owner: Account) -> None:
    """Future callbacks stay off today's list and missing callback times are rejected."""
    company, campaign = business(db, owner)
    with pytest.raises(ValueError, match="date"):
        record_call(
            db, company.id, campaign.id, owner.user.id, "callback", "callback", "Call next week"
        )
    due = utc_now() + timedelta(days=7)
    record_call(
        db,
        company.id,
        campaign.id,
        owner.user.id,
        "callback",
        "callback",
        "Call next week",
        due_at=due,
    )
    assert not call_list(db)
    journey = detail(db, company.id)
    assert journey["stage"] == "Call" and journey["due_at"] == due and not journey["needs_today"]


def test_reviewer_calls_ui_without_settings(
    client: TestClient, db: Database, owner: Account, reviewer: Account
) -> None:
    """Mobile call work remains accessible; settings GET and POST are owner-only."""
    company, campaign = business(db, owner)
    _assign_member(db, owner, reviewer, company.id)
    sign_in(client, reviewer)
    page = client.get("/calls")
    assert page.status_code == 200 and company.name in page.text
    assert 'href="/settings"' not in page.text
    assert client.get("/settings").status_code == 403
    assert client.get("/learning").status_code == 403
    assert (
        client.post("/settings/acquisition", data={"csrf_token": csrf_from(page.text)}).status_code
        == 403
    )
    saved = client.post(
        f"/calls/{company.id}/outcome",
        data={
            "csrf_token": csrf_from(page.text),
            "campaign_id": campaign.id,
            "request_key": "ui-call",
            "outcome": "no_answer",
            "notes": "No answer today",
        },
    )
    assert saved.status_code == 303
    assert "No answer today" in client.get("/calls").text


def test_event_projection_stops_and_pending_followup() -> None:
    """Current stage and next schedule come from event order, with stop precedence."""
    now = utc_now()
    events = [
        LeadEvent(
            id=str(i),
            event_key=str(i),
            company_id="synthetic",
            kind=kind,
            data=data,
            occurred_at=now + timedelta(seconds=i),
        )
        for i, (kind, data) in enumerate(
            [
                ("found", {}),
                ("enriched", {}),
                ("audited", {}),
                ("drafted", {}),
                ("approved", {}),
                ("sent", {"intent_id": "first", "mailbox_id": "box", "channel": "email"}),
                ("followup_sent", {"intent_id": "f1", "step": 1}),
                (
                    "scheduled",
                    {"intent_id": "f2", "step": 2, "due_at": (now + timedelta(days=3)).isoformat()},
                ),
            ]
        )
    ]
    value = derive(events)
    assert value["stage"] == "Follow-ups" and value["followups"] == 1
    assert value["next_action"] == "Follow-up 2 due" and value["mailbox_id"] == "box"
    events.append(
        LeadEvent(
            id="stop",
            event_key="stop",
            company_id="synthetic",
            kind="unsubscribed",
            data={"reason": "Recipient unsubscribed"},
            occurred_at=now + timedelta(minutes=1),
        )
    )
    value = derive(events)
    assert value["stopped_reason"] == "Recipient unsubscribed" and value["due_at"] is None


def _assign_member(db: Database, owner: Account, reviewer: Account, company_id: str) -> None:
    from app.calls.access import assign_calls
    from app.linkedin.identity import add_profile

    profile = add_profile(
        db, reviewer.user.id, "https://linkedin.com/in/call-reviewer", owner.user.id
    )
    assign_calls(db, company_id, profile.id, owner.user.id)
