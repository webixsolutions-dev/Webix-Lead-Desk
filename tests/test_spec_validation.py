"""Owner review regressions for address checks, conservative labels and guidance."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.clock import utc_now
from app.db.lead_models import Campaign, Company, Contact
from app.db.outreach_models import LocalTeamMember, PlaybookVersion
from app.db.session import Database
from app.drafting.service import approve
from app.inbox.parse import classify, parse_reply
from app.playbook.partner import meeting_block, seed_partner
from app.policy.verification import address_block, verify_address
from tests.conftest import Account, sign_in
from tests.delivery_helpers import reply_bytes
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_research import research_company as research_company


@pytest.mark.parametrize(
    "body,label",
    [
        ("I'm not really interested right now.", "not_interested"),
        ("We're not particularly interested in this.", "not_interested"),
        ("Not right now, thank you.", "not_interested"),
        ("No need, we handle this internally.", "not_interested"),
        ("We're all set.", "not_interested"),
        ("We already have someone.", "not_interested"),
        ("We aren’t interested.", "not_interested"),
        ("No thank you, sounds good for another business.", "not_interested"),
        ("No interest in this service.", "not_interested"),
        ("I can't say I'm interested yet.", "needs_review"),
        ("I'm not sure, send me more context.", "needs_review"),
        ("Maybe next quarter.", "needs_review"),
        ("Yes, we are interested.", "interested"),
        ("Sounds good, let's talk.", "interested"),
        ("Please send me more details.", "interested"),
        ("Let's book a call next week.", "interested"),
        ("Please remove me from your list.", "unsubscribe"),
    ],
)
def test_real_world_reply_phrases(body: str, label: str) -> None:
    """Positive keywords never override negative or uncertain language."""
    assert classify(parse_reply(reply_bytes(body))) == label


@pytest.mark.parametrize(
    "local,allow,blocked",
    [
        ("info", False, False),
        ("sales", False, False),
        ("person", False, False),
        ("support", False, True),
        ("support", True, False),
        ("noreply", True, True),
        ("no-reply", True, True),
        ("donotreply", True, True),
    ],
)
def test_role_address_rules(local: str, allow: bool, blocked: bool) -> None:
    """Owner allowance cannot override an undeliverable no-reply address."""
    contact = Contact(email=local + "@example.com", allow_role=allow)
    assert bool(address_block(contact)) == blocked


def test_approval_refreshes_dns_and_keeps_failure_evidence(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Thirty-day/address-bound verification refreshes before exact approval, not after send."""
    draft = make_draft(db, drafting_setup)
    with db.sessions.begin() as session:
        contact = session.get(Contact, drafting_setup["contact_id"])
        assert contact
        contact.dns_checked_at = utc_now() - timedelta(days=31)
    with patch("app.policy.verification.check_mail_route", return_value="no_mail") as dns:
        with pytest.raises(ValueError, match="routing check failed"):
            approve(db, draft.id, draft.digest, drafting_setup["actor_id"])
        assert dns.call_count == 1
    with db.sessions() as session:
        contact = session.get(Contact, drafting_setup["contact_id"])
        assert contact and contact.dns_result == "no_mail" and contact.dns_checked_at
    with patch("app.policy.verification.check_mail_route", return_value="implicit_mx") as dns:
        approve(db, draft.id, draft.digest, drafting_setup["actor_id"])
        verify_address(db, drafting_setup["contact_id"])
        assert dns.call_count == 1  # positive cache reused
    with db.sessions.begin() as session:
        contact = session.get(Contact, drafting_setup["contact_id"])
        assert contact
        contact.email = "changed@example.com"
    with patch("app.policy.verification.check_mail_route", return_value="mx") as dns:
        verify_address(db, drafting_setup["contact_id"])
        dns.assert_called_once_with("changed@example.com")


def test_partner_seed_and_local_meeting_are_owner_data(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Seeding is idempotent; physical meetings need the correct recorded person/location."""
    with db.sessions.begin() as session:
        seed_partner(session, drafting_setup["actor_id"])
        seed_partner(session, drafting_setup["actor_id"])
        versions = list(
            session.scalars(select(PlaybookVersion).where(PlaybookVersion.scope == "partner"))
        )
        assert len(versions) == 1 and "NDA" in versions[0].guidance
        company = session.get(Company, drafting_setup["company_id"])
        campaign = session.get(Campaign, drafting_setup["campaign_id"])
        assert company and campaign
        campaign.lane = "partner"
        assert meeting_block(session, company, campaign, "We can meet you in person.")
        assert not meeting_block(session, company, campaign, "Would a video call help?")
        member = LocalTeamMember(
            name="Example Person",
            city=company.city,
            country=campaign.country,
            active=True,
            reviewed_by=drafting_setup["actor_id"],
        )
        session.add(member)
        session.flush()
        assert not meeting_block(
            session, company, campaign, "Example Person can meet you in person."
        )
        member.city = "Unrelated city"
        assert meeting_block(session, company, campaign, "Example Person can meet you in person.")
        member.city = ""
        assert not meeting_block(
            session, company, campaign, "Example Person can meet you in person."
        )


def test_settings_sections_are_real_and_operations_owner_only(
    client: TestClient, owner: Account, reviewer: Account
) -> None:
    """Consolidated navigation renders the working sections and protects owner operations."""
    sign_in(client, owner)
    page = client.get("/settings")
    assert page.status_code == 200
    assert 'id="learning"' in page.text and 'id="operations"' in page.text
    sign_in(client, reviewer)
    page = client.get("/settings")
    assert 'id="operations"' not in page.text


def test_delay_review_never_schedules_or_overrides_a_reply(
    client: TestClient, owner: Account, db: Database, drafting_setup: dict[str, str]
) -> None:
    """Only a confirmed owner review can clear a delay-only hold; scheduling stays separate."""
    from app.db.delivery_models import SendIntent
    from app.db.outreach_models import Conversation
    from app.inbox.service import record_reply
    from tests.conftest import csrf_from
    from tests.delivery_helpers import NOW, approved_intent

    item = approved_intent(db, drafting_setup)
    raw = (
        "From: mailer-daemon@example.org\nSubject: Delayed\n\n"
        f"Delivery delayed, will retry. Message-ID: {item.message_id}\n"
    ).encode()
    assert record_reply(db, item.mailbox_id, raw, "delay", NOW)
    sign_in(client, owner)
    fields = {
        "csrf_token": csrf_from(client.get("/conversations/inbox").text),
        "reviewed": "true",
        "note": "Checked synthetic mailbox delivery status.",
    }
    route = f"/conversations/{item.conversation_id}/review-delay"
    assert client.post(route, data=fields).status_code == 303
    with db.sessions.begin() as session:
        intent = session.get(SendIntent, item.id)
        conversation = session.get(Conversation, item.conversation_id)
        assert intent and intent.state == "held" and conversation
        conversation.state = "held"
    record_reply(db, item.mailbox_id, reply_bytes("Not interested"), "reply", NOW)
    assert client.post(route, data=fields).status_code == 409
