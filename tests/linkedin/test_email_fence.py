"""Cross-channel policy is exercised through the real SMTP dispatch path with fakes."""

from sqlalchemy import select

from app.core.config import Settings
from app.db.delivery_models import SendIntent
from app.db.linkedin_models import Person
from app.db.linkedin_queue_models import PersonDay
from app.db.session import Database
from app.linkedin.day_fence import reserve_touch
from app.linkedin.identity import confirm_person
from app.sending.service import dispatch
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def test_linkedin_reservation_blocks_smtp_without_consuming_attempt(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """A linked personal inbox is deferred to tomorrow before crossing SMTP DATA."""
    target = confirm_person(
        db,
        drafting_setup["company_id"],
        drafting_setup["actor_id"],
        name="Synthetic Decision Maker",
        role="Owner",
        url="https://linkedin.com/in/synthetic-owner",
        email="service@example.com",
        source_url="https://example.com/team",
        note="Synthetic reviewed team and personal address match",
        presence="active",
    )
    item = approved_intent(db, drafting_setup)
    with db.sessions.begin() as session:
        assert reserve_touch(session, target.id, "linkedin:prepared", "linkedin", NOW)
    transport = FakeTransport()
    dispatch(
        db,
        delivery_setup,
        claim_send(db, delivery_setup, item),
        transport=transport,
        clock=lambda: NOW,
    )
    assert transport.wires == []
    with db.sessions() as session:
        current = session.get(SendIntent, item.id)
        assert current and current.state == "scheduled" and current.attempts == 0
        assert current.hold_reason == "person_already_contacted_today"
        assert current.due_at > NOW


def test_accepted_email_reserves_late_matched_person(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """An identity without contact_id still matches its published personal email at dispatch."""
    target = confirm_person(
        db,
        drafting_setup["company_id"],
        drafting_setup["actor_id"],
        name="Synthetic Decision Maker",
        role="Owner",
        url="https://linkedin.com/in/synthetic-owner",
        email="service@example.com",
        source_url="https://example.com/team",
        note="Synthetic reviewed personal address match",
        presence="active",
    )
    with db.sessions.begin() as session:
        saved = session.get(Person, target.id)
        assert saved
        saved.contact_id = None
    item = approved_intent(db, drafting_setup)
    transport = FakeTransport()
    assert "accepted" in dispatch(
        db,
        delivery_setup,
        claim_send(db, delivery_setup, item),
        transport=transport,
        clock=lambda: NOW,
    )
    with db.sessions.begin() as session:
        assert session.scalar(select(PersonDay.channel)) == "email"
        assert not reserve_touch(session, target.id, "linkedin:second", "linkedin", NOW)
