"""Exact approval, manual sequencing and uncertain-result boundaries."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.linkedin_draft_models import LinkedInDraft
from app.db.linkedin_models import LinkedInTask, Person, Touch
from app.db.linkedin_queue_models import LinkedInSequence
from app.db.outreach_models import Mailbox
from app.db.research_models import Evidence
from app.db.session import Database
from app.linkedin.actions import complete, person_event, resolve
from app.linkedin.drafts import approve, save, valid
from app.linkedin.leases import claim_task
from app.linkedin.planner import plan_day
from app.linkedin.replies import log_reply
from app.linkedin.review import reply_task, review_sequence
from tests.conftest import Account
from tests.linkedin.test_planner import NOW, seed

START = NOW + timedelta(hours=2)


def invitation(db: Database, owner: Account) -> tuple[str, str, LinkedInDraft]:
    """Prepare a blank invitation for a synthetic, explicitly assigned member."""
    _, person_id = seed(db, owner)
    plan_day(db, NOW)
    with db.sessions() as session:
        task = session.scalar(select(LinkedInTask))
        assert task
        task_id = task.id
    draft = save(
        db, task_id, owner.user.id, "", angle="Local professional network", noncommercial=True
    )
    return task_id, person_id, draft


def message(
    db: Database, task_id: str, owner: Account, body: str, *, previous: str | None = None
) -> LinkedInDraft:
    """Use synthetic reviewed facts and an explicit, expiring LinkedIn consent record."""
    with db.sessions.begin() as session:
        task = session.get(LinkedInTask, task_id)
        assert task
        box = session.scalar(select(Mailbox))
        if not box:
            box = Mailbox(
                email="sales@example.test",
                provider="titan",
                lane="standard",
                brand="Example Agency",
                sender_name="Example Member",
                mailing_address="123 Example Street, Calgary",
                contact_details="sales@example.test",
            )
            session.add(box)
        fact = Evidence(
            company_id=task.company_id,
            kind="human_call",
            source_url="",
            facts={
                "observation": "The owner asked for a LinkedIn message about their booking process."
            },
            reviewed_by=owner.user.id,
        )
        session.add(fact)
        session.flush()
    return save(
        db,
        task_id,
        owner.user.id,
        body,
        mailbox_id=box.id,
        evidence_ids=[fact.id],
        basis={
            "kind": "express_consent",
            "note": "The person specifically requested this information through LinkedIn.",
            "expires_at": (NOW + timedelta(days=360)).isoformat(),
        },
        angle="Booking process review",
        previous_id=previous,
    )


def claim(db: Database, task_id: str, owner: Account, now: datetime = START) -> str:
    """Start a human action through the real approval validator."""
    token = claim_task(db, task_id, owner.user.id, now, approved=lambda s, t: valid(s, t, now))
    assert token
    return token


def test_unapproved_edit_and_changed_recipient_cannot_be_claimed(
    db: Database, owner: Account, reviewer: Account
) -> None:
    """Exact approvals do not survive an edit or a changed identity."""
    task_id, person_id, draft = invitation(db, owner)
    assert claim_task(db, task_id, owner.user.id, START, approved=valid) is None
    with pytest.raises(ValueError):
        approve(db, draft.id, draft.digest, reviewer.user.id)
    approve(db, draft.id, draft.digest, owner.user.id)
    edited = save(
        db,
        task_id,
        owner.user.id,
        "",
        angle="New professional connection",
        noncommercial=True,
        previous_id=draft.id,
    )
    assert claim_task(db, task_id, owner.user.id, START, approved=valid) is None
    with pytest.raises(ValueError):
        approve(db, draft.id, draft.digest, owner.user.id)
    approve(db, edited.id, edited.digest, owner.user.id)
    with db.sessions.begin() as session:
        person = session.get(Person, person_id)
        assert person
        person.linkedin_url = "https://www.linkedin.com/in/changed-person"
    assert claim_task(db, task_id, owner.user.id, START, approved=valid) is None


def test_drafts_and_approval_records_are_database_immutable(db: Database, owner: Account) -> None:
    """SQL edits cannot silently rewrite the owner's approved text or attribution."""
    _, _, draft = invitation(db, owner)
    approve(db, draft.id, draft.digest, owner.user.id)
    for sql in (
        "UPDATE linkedin_drafts SET digest='changed'",
        "DELETE FROM linkedin_drafts",
        "UPDATE linkedin_approvals SET digest='changed'",
        "DELETE FROM linkedin_approvals",
    ):
        with pytest.raises(DBAPIError), db.engine.begin() as conn:
            conn.execute(text(sql))


def test_manual_sequence_acceptance_and_idempotent_completion(db: Database, owner: Account) -> None:
    """Acceptance schedules a first message after two days, never auto-sends it."""
    task_id, person_id, draft = invitation(db, owner)
    approve(db, draft.id, draft.digest, owner.user.id)
    token = claim(db, task_id, owner)
    complete(db, task_id, owner.user.id, token, START)
    complete(db, task_id, owner.user.id, token, START)
    person_event(db, person_id, owner.user.id, "accepted", START + timedelta(hours=1))
    with db.sessions() as session:
        seq = session.get(LinkedInSequence, person_id)
        assert seq and seq.state == "accepted" and seq.next_action == "message1"
        assert seq.next_due == START + timedelta(days=2, hours=1)
        assert len(list(session.scalars(select(Touch).where(Touch.direction == "outbound")))) == 1
    assert plan_day(db, START + timedelta(days=1)) == 0
    plan_day(db, START + timedelta(days=2, hours=2))
    with db.sessions() as session:
        next_task = session.scalar(select(LinkedInTask).where(LinkedInTask.action == "message1"))
        assert next_task and next_task.state == "pending" and not next_task.approval_digest


def test_expired_claim_never_retries_and_requires_checked_resolution(
    db: Database, owner: Account
) -> None:
    """A lost browser or PC session cannot produce another automatic invitation."""
    task_id, _, draft = invitation(db, owner)
    approve(db, draft.id, draft.digest, owner.user.id)
    token = claim(db, task_id, owner)
    later = START + timedelta(minutes=20)
    plan_day(db, later)
    with pytest.raises(ValueError):
        complete(db, task_id, owner.user.id, token, later)
    assert claim_task(db, task_id, owner.user.id, later, approved=valid) is None
    resolve(
        db, task_id, owner.user.id, "not_sent", "Checked the profile; no invitation exists.", later
    )
    assert claim_task(db, task_id, owner.user.id, later, approved=valid) is None
    fresh = save(
        db,
        task_id,
        owner.user.id,
        "",
        angle="Reviewed invitation",
        noncommercial=True,
        previous_id=draft.id,
    )
    assert fresh.state == "draft"


def test_reply_cancels_action_and_reconciliation_cannot_resume_sequence(
    db: Database, owner: Account
) -> None:
    """A reply during a manual action stops all pending company work."""
    task_id, person_id, draft = invitation(db, owner)
    approve(db, draft.id, draft.digest, owner.user.id)
    token = claim(db, task_id, owner)
    log_reply(
        db,
        person_id,
        owner.user.id,
        "real-message",
        "interested",
        "Please send details through LinkedIn.",
    )
    with pytest.raises(ValueError):
        complete(db, task_id, owner.user.id, token, START)
    resolve(
        db,
        task_id,
        owner.user.id,
        "sent",
        "Invitation was completed just before the incoming reply.",
        START,
    )
    with db.sessions() as session:
        seq = session.get(LinkedInSequence, person_id)
        assert seq and seq.state == "replied"
    assert plan_day(db, START + timedelta(days=1)) == 0
    reply = reply_task(db, person_id, owner.user.id, START)
    copy = message(
        db,
        reply.id,
        owner,
        "Thanks for your reply. Which part of the booking process would you like to discuss?",
    )
    approve(db, copy.id, copy.digest, owner.user.id)
    plan_day(db, START + timedelta(days=1))
    token2 = claim(db, reply.id, owner, START + timedelta(days=1, hours=3))
    complete(db, reply.id, owner.user.id, token2, START + timedelta(days=1, hours=3))
    with db.sessions() as session:
        seq = session.get(LinkedInSequence, person_id)
        assert seq and seq.state == "replied"


@pytest.mark.parametrize(
    "body",
    [
        "We guarantee more bookings for you.",
        "Our package costs CAD 99 this month.",
        "Visit example.com for details.",
    ],
)
def test_first_message_rejects_prices_guarantees_and_links(
    db: Database, owner: Account, body: str
) -> None:
    """Content gates also apply outside email and cannot be bypassed by owner approval."""
    task_id, _, _ = invitation(db, owner)
    with db.sessions.begin() as session:
        task = session.get(LinkedInTask, task_id)
        assert task
        task.action = "message1"
    with pytest.raises(ValueError):
        message(db, task_id, owner, body)


def test_final_message_closes_for_ninety_days_and_requires_new_angle(
    db: Database, owner: Account
) -> None:
    """Silence never restarts the old campaign and the member lock survives closure."""
    task_id, person_id, old = invitation(db, owner)
    with db.sessions.begin() as session:
        task = session.get(LinkedInTask, task_id)
        assert task
        task.action = "message3"
    draft = message(
        db,
        task_id,
        owner,
        "I will close the loop here. Thanks for taking a look, "
        "and feel free to reach out when it suits you.",
        previous=old.id,
    )
    approve(db, draft.id, draft.digest, owner.user.id)
    token = claim(db, task_id, owner)
    complete(db, task_id, owner.user.id, token, START)
    with pytest.raises(ValueError, match="cooldown"):
        review_sequence(
            db,
            person_id,
            owner.user.id,
            "restart",
            "A different project and reviewed service angle.",
            START + timedelta(days=21),
        )
    review_sequence(
        db,
        person_id,
        owner.user.id,
        "restart",
        "A different project and reviewed service angle.",
        START + timedelta(days=91),
    )
    with db.sessions() as session:
        seq = session.get(LinkedInSequence, person_id)
        assert seq and seq.generation == 2


def test_ownership_and_window_enforced_even_with_approved_text(
    db: Database, owner: Account, reviewer: Account
) -> None:
    """No colleague can execute another member's action or bypass the workday."""
    task_id, _, draft = invitation(db, owner)
    approve(db, draft.id, draft.digest, owner.user.id)
    assert claim_task(db, task_id, reviewer.user.id, START, approved=valid) is None
    assert claim_task(db, task_id, owner.user.id, NOW, approved=valid) is None


def test_changed_evidence_or_expired_basis_invalidates_an_approved_message(
    db: Database, owner: Account
) -> None:
    """A previously approved direct message is rechecked at action time."""
    task_id, _, old = invitation(db, owner)
    with db.sessions.begin() as session:
        task = session.get(LinkedInTask, task_id)
        assert task
        task.action = "message2"
    draft = message(
        db,
        task_id,
        owner,
        (
            "Here is the short concept we discussed. "
            "Would you like me to walk through the proposed booking steps?"
        ),
        previous=old.id,
    )
    approve(db, draft.id, draft.digest, owner.user.id)
    with db.sessions() as session:
        task = session.get(LinkedInTask, task_id)
        assert task and valid(session, task, START)
        assert not valid(session, task, NOW + timedelta(days=361))
    with db.sessions.begin() as session:
        fact = session.get(Evidence, draft.snapshot["evidence"][0]["id"])
        assert fact
        fact.facts = {"observation": "This fact was corrected after approval"}
    assert claim_task(db, task_id, owner.user.id, START, approved=valid) is None


def test_not_interested_has_twelve_month_hold_and_stop_is_permanent(
    db: Database, owner: Account
) -> None:
    """A new angle cannot prematurely override a refusal or any permanent opt-out."""
    from app.core.clock import utc_now

    task_id, person_id, _ = invitation(db, owner)
    log_reply(db, person_id, owner.user.id, "no", "not_interested", "Not interested, thanks.")
    with pytest.raises(ValueError, match="cooldown"):
        review_sequence(
            db,
            person_id,
            owner.user.id,
            "restart",
            "A different reviewed angle after the right waiting period.",
            utc_now() + timedelta(days=90),
        )
    review_sequence(
        db,
        person_id,
        owner.user.id,
        "restart",
        "A different reviewed angle after the right waiting period.",
        utc_now() + timedelta(days=366),
    )
    log_reply(db, person_id, owner.user.id, "stop", "stop", "Please stop all messages.")
    with pytest.raises(ValueError):
        review_sequence(
            db,
            person_id,
            owner.user.id,
            "restart",
            "No angle can override a permanent stop instruction.",
            utc_now() + timedelta(days=800),
        )
