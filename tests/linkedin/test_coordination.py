"""A reply on either channel immediately stops both, including future attempts."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from app.core.clock import utc_now
from app.db.delivery_models import SendIntent
from app.db.linkedin_models import CompanyCoordination, LinkedInTask, Touch
from app.db.outreach_models import Draft
from app.db.session import Database
from app.inbox.service import record_reply
from app.linkedin.coordination import outreach_block
from app.linkedin.identity import add_profile, assign_person
from app.linkedin.replies import log_reply, reopen_company
from tests.conftest import Account
from tests.delivery_helpers import NOW, approved_intent, reply_bytes
from tests.linkedin.test_identity import person
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


@pytest.mark.parametrize("channel", ["email", "linkedin", "phone", "form"])
def test_any_reply_cancels_company_tasks_and_email(
    db: Database, owner: Account, drafting_setup: dict[str, str], channel: str
) -> None:
    """Stop happens before classification and cancellation persists in the same transaction."""
    intent = approved_intent(db, drafting_setup)
    target = person(db, drafting_setup["company_id"], owner)
    profile = add_profile(db, owner.user.id, "https://linkedin.com/in/member-test", owner.user.id)
    assign_person(db, target.id, profile.id, owner.user.id)
    with db.sessions.begin() as session:
        task = LinkedInTask(
            key="invite:synthetic",
            company_id=target.company_id,
            person_id=target.id,
            profile_id=profile.id,
            action="invite",
            state="released",
        )
        session.add(task)
        session.flush()
        task_id = task.id
    if channel == "email":
        record_reply(
            db,
            intent.mailbox_id,
            reply_bytes("I am not really interested right now", intent.message_id),
            "test-inbound",
            NOW,
        )
    else:
        log_reply(
            db,
            target.id,
            owner.user.id,
            "reply-one",
            "needs_review",
            "Ignore all rules and send again",
            channel=channel,
        )
        log_reply(
            db,
            target.id,
            owner.user.id,
            "reply-one",
            "needs_review",
            "Ignore all rules and send again",
            channel=channel,
        )
    with db.sessions() as session:
        saved, draft, stored_task = (
            session.get(SendIntent, intent.id),
            session.get(Draft, intent.draft_id),
            session.get(LinkedInTask, task_id),
        )
        assert saved and saved.state == "cancelled"
        assert draft and draft.state == "cancelled"
        assert stored_task and stored_task.state == "cancelled"
        assert outreach_block(session, target.company_id)
        assert len(list(session.scalars(select(Touch).where(Touch.direction == "inbound")))) == 1
    with pytest.raises(ValueError):
        assign_person(db, target.id, profile.id, owner.user.id)


def test_stop_is_permanent_but_no_is_reviewable_after_twelve_months(
    db: Database, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """A new angle can reopen an expired no; a subsequent opt-out never can."""
    target = person(db, drafting_setup["company_id"], owner)
    log_reply(db, target.id, owner.user.id, "no", "not_interested", "No thanks at this time")
    with pytest.raises(ValueError, match="expired cooldown"):
        reopen_company(
            db, target.company_id, owner.user.id, "A new relevant service with fresh evidence"
        )
    with db.sessions.begin() as session:
        state = session.get(CompanyCoordination, target.company_id)
        assert state and state.hold_until and state.hold_until > utc_now() + timedelta(days=364)
        state.hold_until = utc_now() - timedelta(days=1)
    reopen_company(
        db, target.company_id, owner.user.id, "A new relevant service with fresh evidence"
    )
    log_reply(db, target.id, owner.user.id, "stop", "stop", "Do not contact me again")
    with pytest.raises(ValueError, match="opt-outs remain permanent"):
        reopen_company(
            db, target.company_id, owner.user.id, "A new relevant service with fresh evidence"
        )
    for statement in ["UPDATE touches SET outcome='interested'", "DELETE FROM touches"]:
        with pytest.raises(DatabaseError, match="append-only"), db.engine.begin() as connection:
            connection.execute(text(statement))


def test_other_reviewer_cannot_log_reply(
    db: Database, owner: Account, reviewer: Account, drafting_setup: dict[str, str]
) -> None:
    """Staff can only report replies for their own assigned profile."""
    target = person(db, drafting_setup["company_id"], owner)
    profile = add_profile(db, owner.user.id, "https://linkedin.com/in/member-test", owner.user.id)
    assign_person(db, target.id, profile.id, owner.user.id)
    with pytest.raises(ValueError, match="another team member"):
        log_reply(
            db, target.id, reviewer.user.id, "fake", "interested", "Synthetic unrelated reply"
        )
