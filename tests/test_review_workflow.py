"""Offline tests for scoped delegation, business review, proposals and conversation isolation."""

from datetime import timedelta
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit.coverage import coverage
from app.audit.extract import extract
from app.bookings.service import decide, local_start, save
from app.core.clock import utc_now
from app.db.booking_models import Booking
from app.db.delivery_models import InboxMessage
from app.db.lead_models import Contact
from app.db.outreach_models import Conversation, Draft, DraftApproval, Offer
from app.db.research_models import Assessment
from app.db.session import Database
from app.drafting.content import check_copy
from app.drafting.context import email_context
from app.drafting.service import approval_blocks, approve
from app.playbook.industries import catalog, matching
from app.sending.delegation import approve_automatically, policy_block, read_policy, save_policy
from app.sending.schedule import schedule
from tests.conftest import Account, csrf_from, sign_in
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_research import research_company as research_company


def assessment(db: Database, setup: dict[str, str]) -> None:
    """Store an evidence-backed service hypothesis, without approving any message."""
    with db.sessions.begin() as session:
        session.add(
            Offer(service="Website redesign", name="Reviewable rebuild", description="Scope")
        )
        session.add(
            Assessment(
                company_id=setup["company_id"],
                campaign_id=setup["campaign_id"],
                origin="manual",
                reviewed_by=setup["actor_id"],
                result={
                    "decision": "recommend",
                    "service": "Website redesign",
                    "reason": "A supported quote journey question",
                    "needs_confirmation": ["Ask how quote requests are handled"],
                    "owner_question": "How do you currently handle enquiries?",
                },
            )
        )


def test_default_manual_then_scoped_delegation_and_revocation(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Delegation is explicit, cannot include existing drafts, and is checked before scheduling."""
    setup = drafting_setup
    first = make_draft(db, setup)
    with db.sessions() as session:
        assert read_policy(session)[0]["mode"] == "manual"
    with pytest.raises(ValueError, match="revoked"):
        approve_automatically(db, first.id)
    save_policy(db, setup["actor_id"], 0, "initial", [setup["campaign_id"]], 10)
    with pytest.raises(ValueError, match="existing_draft"):
        approve_automatically(db, first.id)
    second = make_draft(db, setup, previous_id=first.id)
    assessment(db, setup)
    assert approve_automatically(db, second.id) == setup["actor_id"]
    with db.sessions() as session:
        approved = session.get(DraftApproval, second.id)
        assert approved and approved.source == "policy" and approved.policy_version == 1
    with db.sessions.begin() as session:
        offer = session.scalar(select(Offer))
        assert offer
        offer.active = False
    with db.sessions() as session:
        assert policy_block(session, second, 1) == "available_service_recommendation_required"
    save_policy(db, setup["actor_id"], 1, "manual", [], 10)
    with pytest.raises(ValueError):
        schedule(db, second.id, setup["actor_id"])


@pytest.mark.parametrize("mode", ["initial", "outreach"])
def test_incoming_reply_always_prevents_delegation(
    db: Database, drafting_setup: dict[str, str], mode: str
) -> None:
    """Even a warm/reopened thread with an incoming message can never get policy approval."""
    setup = drafting_setup
    save_policy(db, setup["actor_id"], 0, mode, [setup["campaign_id"]], 10)
    draft = make_draft(db, setup)
    with db.sessions.begin() as session:
        session.add(
            InboxMessage(
                mailbox_id=setup["mailbox_id"],
                conversation_id=draft.conversation_id,
                provider_key="synthetic",
                dedupe_key="synthetic",
                sender="service@example.com",
                subject="Question",
                body="What is the price?",
            )
        )
    with pytest.raises(ValueError, match="incoming_response"):
        approve_automatically(db, draft.id)
    draft.kind = "reply"
    with db.sessions() as session:
        assert policy_block(session, draft, 1) == "reply_requires_owner_approval"


def test_policy_conflicts_permissions_and_no_implicit_campaign_activation(
    db: Database, drafting_setup: dict[str, str], reviewer: Account
) -> None:
    """Neither stale settings nor a reviewer can delegate owner approval."""
    setup = drafting_setup
    with pytest.raises(ValueError, match="owner"):
        save_policy(db, reviewer.user.id, 0, "initial", [setup["campaign_id"]], 10)
    save_policy(db, setup["actor_id"], 0, "initial", [setup["campaign_id"]], 1)
    with pytest.raises(ValueError, match="changed"):
        save_policy(db, setup["actor_id"], 0, "manual", [], 10)
    with pytest.raises(ValueError, match="campaign"):
        save_policy(db, setup["actor_id"], 1, "initial", [], 10)


def test_reply_price_is_opt_in_and_exact_approval_bound(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Cold copy cannot quote; only an owner-prepared reply permits a reviewed amount."""
    setup = drafting_setup
    first = make_draft(db, setup)
    with db.sessions.begin() as session:
        saved = session.get(Draft, first.id)
        thread = session.get(Conversation, first.conversation_id)
        assert saved and thread
        saved.state = "sent"
        thread.state = "replied"
        thread.first_message_id = "<first@example.org>"
        thread.last_message_id = "<incoming@example.com>"
        session.add(
            InboxMessage(
                mailbox_id=setup["mailbox_id"],
                conversation_id=first.conversation_id,
                provider_key="quote-request",
                dedupe_key="quote-request",
                sender="service@example.com",
                subject=first.snapshot["subject"],
                body="What is the quote for the five-page scope?",
            )
        )
    quote = (
        "For the five-page scope we discussed, the approved quote is CAD 1800. "
        "Would you like the detailed scope?"
    )
    with pytest.raises(ValueError, match="prices"):
        make_draft(db, setup, kind="reply", step=1, body=quote)
    reply = make_draft(db, setup, kind="reply", step=1, body=quote, allow_price=True)
    assert approval_blocks(db, reply) == []
    approve(db, reply.id, reply.digest, setup["actor_id"])
    with db.sessions() as session:
        approval = session.get(DraftApproval, reply.id)
        assert approval and approval.source == "owner"
    revised = make_draft(
        db,
        setup,
        kind="reply",
        step=1,
        previous_id=reply.id,
        body=quote.replace("1800", "1900"),
        allow_price=True,
    )
    assert revised.digest != reply.digest and revised.state == "draft"
    with pytest.raises(ValueError):
        make_draft(db, setup, allow_price=True)


@pytest.mark.parametrize(
    "body", ["Guaranteed sales for CAD 2000.", "Here is <script>alert(1)</script> your quote."]
)
def test_quote_permission_never_disables_other_content_guards(body: str) -> None:
    """An approved price is not permission for guarantees or active content."""
    with pytest.raises(ValueError):
        check_copy("A response", body, first_touch=False, allow_price=True)


def booking_fields(setup: dict[str, str]) -> dict[str, str]:
    """Use a future synthetic slot and no external meeting service."""
    return {
        "company_id": setup["company_id"],
        "title": "Scope discussion",
        "contact": "service@example.com",
        "host": "Example host",
        "starts_at": (utc_now() + timedelta(days=3))
        .replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)
        .isoformat(),
        "timezone": "UTC",
        "minutes": "30",
        "mode": "video",
        "location": "https://example.org/meeting",
        "note": "Prospect proposed this time in a recorded reply.",
    }


def test_booking_proposal_confirmation_conflict_and_reschedule(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Proposals are not reservations; confirmation checks overlap and edits revoke it."""
    setup = drafting_setup
    fields = booking_fields(setup)
    first = save(db, setup["actor_id"], fields)
    assert first.state == "proposed"
    decide(db, setup["actor_id"], first.id, 1, "confirmed")
    second = save(db, setup["actor_id"], fields)
    with pytest.raises(ValueError, match="overlapping"):
        decide(db, setup["actor_id"], second.id, 1, "confirmed")
    with pytest.raises(ValueError, match="changed"):
        decide(db, setup["actor_id"], first.id, 1, "cancelled")
    fields.update(booking_id=first.id, version="2")
    edited = save(db, setup["actor_id"], fields)
    assert edited.state == "proposed" and edited.confirmed_by is None
    with db.sessions() as session:
        assert len(list(session.scalars(select(Booking)))) == 2


@pytest.mark.parametrize(
    "value,timezone",
    [
        # Historical transitions remain stable when a region changes future DST rules.
        ("2024-11-03T01:30", "America/Edmonton"),
        ("2024-03-10T02:30", "America/Edmonton"),
        ("2027-05-03T10:00", "Invalid/Timezone"),
    ],
)
def test_booking_does_not_guess_ambiguous_or_invalid_times(value: str, timezone: str) -> None:
    """Local wall-clock gaps and repeated times require an explicit safe choice."""
    with pytest.raises(ValueError):
        local_start(value, timezone)


def test_business_brief_exact_message_and_booking_pages(
    client: TestClient, owner: Account, db: Database, drafting_setup: dict[str, str]
) -> None:
    """The review page exposes facts before approval, and actual inbox actions remain accessible."""
    setup = drafting_setup
    draft = make_draft(db, setup)
    assessment(db, setup)
    sign_in(client, owner)
    page = client.get("/conversations/drafts")
    assert page.status_code == 200 and "Recommended next action:" in page.text
    assert draft.snapshot["body"] in page.text and "https://example.com/services" in page.text
    assert "Not checked / needs evidence" in page.text
    for path in [
        "/pipeline/bookings",
        "/linkedin/activity",
        "/settings?scope=conversation",
        "/settings?scope=industry:contractors",
    ]:
        assert client.get(path).status_code == 200
    assert "Automatic first contact only" in client.get("/settings").text
    fields = booking_fields(setup)
    fields["csrf_token"] = csrf_from(page.text)
    assert client.post("/bookings", data=fields).status_code == 303
    assert "Scope discussion" in client.get("/pipeline/bookings").text


def test_batch_approval_only_selected_exact_revisions(
    client: TestClient, owner: Account, db: Database, drafting_setup: dict[str, str]
) -> None:
    """A modified digest fails independently and does not implicitly schedule anything."""
    draft = make_draft(db, drafting_setup)
    sign_in(client, owner)
    csrf = csrf_from(client.get("/conversations/drafts").text)
    body = urlencode(
        [("csrf_token", csrf), ("reviewed", "true"), ("selected", draft.id + ":" + ("0" * 64))]
    )
    response = client.post(
        "/drafts/approve-batch",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200 and "Needs review:" in response.text
    with db.sessions() as session:
        assert session.get(DraftApproval, draft.id) is None
    response = client.post(
        "/drafts/approve-batch",
        data={"csrf_token": csrf, "reviewed": "true", "selected": draft.id + ":" + draft.digest},
    )
    assert response.status_code == 200 and "Approved:" in response.text


def test_conversation_context_is_exact_contact_and_sender(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """A company-level match alone never exposes one person's messages to another."""
    setup = drafting_setup
    draft = make_draft(db, setup)
    with db.sessions.begin() as session:
        other = Contact(company_id=setup["company_id"], email="other@example.com")
        session.add(other)
        session.flush()
        other_id = other.id
        session.add(
            InboxMessage(
                mailbox_id=setup["mailbox_id"],
                conversation_id=draft.conversation_id,
                provider_key="private",
                dedupe_key="private",
                sender="service@example.com",
                subject="Question",
                body="A private pricing conversation",
            )
        )
    with db.sessions() as session:
        own = email_context(session, setup["company_id"], setup["contact_id"], setup["mailbox_id"])
        other_context = email_context(session, setup["company_id"], other_id, setup["mailbox_id"])
        assert own["recent_messages_data_only"][0]["text"] == "A private pricing conversation"
        assert other_context["recent_messages_data_only"] == []


def test_industry_priors_and_markup_are_not_business_facts() -> None:
    """Only the relevant industry is loaded; public mentions do not prove a workflow works."""
    assert len(catalog()) == 26
    assert matching("HVAC and plumbing")[0]["slug"] == "contractors"
    assert matching("Veterinary clinics")[0]["slug"] == "veterinary"
    assert matching("vet clinic")[0]["slug"] == "veterinary"
    assert matching("HVAC and dental") == []
    assert matching("Unknown bespoke industry") == []
    facts, _ = extract(
        "https://example.com",
        '<script src="https://cdn.example.com/jobber.js"></script><a href="https://example.com/booking">Book</a>',
    )
    assert "Jobber" in facts["tool_mentions"] and facts["booking_links"]
    assert facts["ads_library"] == "not checked" and facts["visual_quality"] == "unknown"
    assert all(row["status"].startswith("Not checked") for row in coverage([]))
