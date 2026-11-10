"""Owner queue priority, exact-person isolation, canonical routes and guarded actions."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.clock import utc_now
from app.db.delivery_models import InboxMessage, SendIntent
from app.db.lead_models import Campaign, Company
from app.db.models import AuditEvent, Job
from app.db.session import Database
from app.reporting.activity import activity
from app.reporting.conversations import thread, threads
from app.reporting.work_queue import GROUPS, queue
from tests.conftest import Account, csrf_from, sign_in
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_research import research_company as research_company


def add_message(
    db: Database,
    setup: dict[str, str],
    conversation_id: str,
    sender: str = "service@example.com",
    key: str = "reply",
) -> InboxMessage:
    """Record only synthetic inbox evidence, without invoking a transport."""
    with db.sessions.begin() as session:
        row = InboxMessage(
            mailbox_id=setup["mailbox_id"],
            conversation_id=conversation_id,
            provider_key=key,
            dedupe_key=key,
            sender=sender,
            subject="Scope question",
            body="Can you explain the booking workflow? <script>untrusted</script>",
            classification="question",
            received_at=utc_now() - timedelta(hours=2),
        )
        session.add(row)
        session.flush()
        return row


def test_queue_order_review_and_exact_sender_identity(
    db: Database, drafting_setup: dict[str, str], client: TestClient, owner: Account
) -> None:
    """Reviewing one real reply leaves different senders separate and never sends anything."""
    draft = make_draft(db, drafting_setup)
    message = add_message(db, drafting_setup, draft.conversation_id)
    other = add_message(db, drafting_setup, draft.conversation_id, "other@example.test", "other")
    result = queue(db, utc_now(), "Asia/Karachi", [], [])
    assert [g["key"] for g in result["work_groups"]] == [g[0] for g in GROUPS]
    assert result["work_groups"][0]["count"] == 2
    assert result["work_groups"][3]["count"] == 1
    sign_in(client, owner)
    page = client.get("/command/replies/email/" + message.id)
    assert page.status_code == 200 and "Prepare or edit reply" in page.text
    assert "&lt;script&gt;untrusted" in page.text
    wrong = client.get("/command/replies/email/" + other.id)
    assert "Prepare or edit reply" not in wrong.text
    assert "Confirm this sender" in wrong.text
    url = "/command/replies/email/" + message.id + "/reviewed"
    assert client.post(url, data={"reason": "Reviewed manually"}).status_code == 403
    fields = {"csrf_token": csrf_from(page.text), "reason": "Reviewed manually"}
    assert client.post(url, data=fields).status_code == 303
    assert client.post(url, data=fields).status_code == 303
    assert queue(db, utc_now(), "Asia/Karachi", [], [])["reply_count"] == 1
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SendIntent)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "queue.email.reviewed")
            )
            == 1
        )
    cards = threads(db, utc_now())
    assert len(cards) == 2
    history = thread(db, contact_id=drafting_setup["contact_id"])
    assert {m["sender"] for m in history["thread_messages"]} == {"service@example.com"}
    assert client.get("/conversations/contact/" + drafting_setup["contact_id"]).status_code == 200
    assert client.get("/conversations?tab=unknown").status_code == 422


@pytest.mark.parametrize(
    "old,new",
    [
        ("/outbox", "/conversations/drafts"),
        ("/bookings", "/pipeline/bookings"),
        ("/calls", "/leads/calls"),
        ("/demos", "/pipeline/demos"),
        ("/partners", "/leads/results"),
        ("/inbox", "/conversations/inbox"),
    ],
)
def test_legacy_get_redirects_preserve_queries(
    client: TestClient, owner: Account, old: str, new: str
) -> None:
    """Bookmarks and existing POST redirects retain their selected record."""
    sign_in(client, owner)
    response = client.get(old + "?company=synthetic")
    assert (
        response.status_code == 307 and response.headers["location"] == new + "?company=synthetic"
    )
    assert client.get(new).status_code == 200


def test_owner_only_queue_staff_calls_and_calgary_scope(
    client: TestClient,
    db: Database,
    owner: Account,
    reviewer: Account,
    research_company: tuple[Company, Campaign],
) -> None:
    """Staff see their calling work, not owner finances, conversations or activity."""
    sign_in(client, reviewer)
    assert client.get("/leads/calls").status_code == 200
    for path in ["/command/activity", "/conversations", "/leads/campaigns", "/pipeline/bookings"]:
        assert client.get(path).status_code == 403
    client.cookies.clear()
    sign_in(client, owner)
    _, campaign = research_company
    with db.sessions.begin() as session:
        row = session.get(Campaign, campaign.id)
        assert row
        row.area = "Toronto, Ontario"
    fields = {
        "csrf_token": csrf_from(client.get("/").text),
        "campaign_id": campaign.id,
        "category": "CONTRACTOR",
    }
    assert client.post("/sources/calgary", data=fields).status_code == 422
    assert "Open Calgary licence data" not in client.get("/leads").text
    assert "Sending times follow Calgary time" in client.get("/leads/campaigns").text
    assert "timezone not configured" in client.get("/?campaign=" + campaign.id).text
    with db.sessions() as session:
        assert session.scalar(select(Job)) is None


def test_progress_counts_businesses_once_and_activity_does_not_mutate(
    db: Database, drafting_setup: dict[str, str], client: TestClient, owner: Account
) -> None:
    """Multiple draft revisions do not inflate campaign progress or trigger background work."""
    old = make_draft(db, drafting_setup)
    make_draft(db, drafting_setup, previous_id=old.id)
    progress = activity(db, utc_now())["campaign_progress"][0]["counts"]
    assert progress["Found"] == 1 and progress["Enriched"] == 1 and progress["Drafted"] == 1
    assert progress["Approved"] == 0 and progress["Sent"] == 0
    sign_in(client, owner)
    assert client.get("/command/activity").status_code == 200
    with db.sessions() as session:
        assert session.scalar(select(Job)) is None


def test_linkedin_reply_member_totals_and_bookings(
    db: Database, client: TestClient, owner: Account
) -> None:
    """Actual member replies, named tasks and confirmed appointments share the owner queue."""
    from app.db.booking_models import Booking
    from app.db.linkedin_models import LinkedInTask, Person, Touch
    from tests.linkedin.test_drafts_actions import invitation

    task_id, person_id, _ = invitation(db, owner)
    now = utc_now()
    with db.sessions.begin() as session:
        person = session.get(Person, person_id)
        task = session.get(LinkedInTask, task_id)
        assert person and task
        touch = Touch(
            event_key="synthetic-reply",
            company_id=person.company_id,
            person_id=person.id,
            profile_id=task.profile_id,
            channel="linkedin",
            direction="inbound",
            outcome="question",
            text="Tell me about the booking workflow.",
            occurred_at=now - timedelta(minutes=15),
        )
        booking = Booking(
            company_id=person.company_id,
            title="Example scope discussion",
            contact="Example Person",
            host="Demo Owner",
            starts_at=now + timedelta(minutes=30),
            ends_at=now + timedelta(minutes=50),
            timezone="UTC",
            state="confirmed",
            created_by=owner.user.id,
        )
        session.add_all([touch, booking])
        session.flush()
    result = queue(db, now, "UTC", [], [])
    assert result["reply_count"] == 1
    assert result["work_groups"][1]["count"] == 1
    assert result["work_groups"][5]["count"] == 1
    assert "pending tasks" in result["work_groups"][5]["items"][0]["detail"]
    sign_in(client, owner)
    response = client.get("/command/replies/linkedin/" + touch.id)
    assert response.status_code == 200 and touch.text in response.text
    assert client.get("/conversations/people/" + person_id).status_code == 200
    assert client.get("/pipeline/bookings?booking=" + booking.id).status_code == 200
    assert client.get("/linkedin/plan?profile=" + task.profile_id).status_code == 200
    action = "/command/replies/linkedin/" + touch.id + "/reviewed"
    assert (
        client.post(
            action,
            data={"csrf_token": csrf_from(response.text), "reason": "Read and handled manually"},
        ).status_code
        == 303
    )
    assert queue(db, now, "UTC", [], [])["reply_count"] == 0
    assert client.get("/command/replies/other/" + touch.id).status_code == 404
