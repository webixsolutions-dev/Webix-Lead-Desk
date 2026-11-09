"""Bounded query growth for the hosted dashboard's observed angle report."""

from datetime import date, timedelta

import pytest
from sqlalchemy import event

from app.db.linkedin_draft_models import LinkedInDraft
from app.db.linkedin_models import LinkedInTask, Person, Touch
from app.db.models import new_id
from app.db.session import Database
from app.drafting.content import fingerprint
from app.linkedin import metrics
from tests.conftest import Account
from tests.linkedin.test_metrics_learning import NOW, START, cohort


def test_angle_report_batches_provenance_instead_of_querying_each_contact(
    db: Database, owner: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fifty first messages should need a small fixed query set on remote PostgreSQL too."""
    profile_id, people = cohort(db, owner, count=50, accepted=10)
    monkeypatch.setattr(metrics, "utc_now", lambda: NOW)
    with db.sessions.begin() as session:
        for person_id in people:
            person = session.get(Person, person_id)
            assert person
            task = LinkedInTask(
                key=new_id(),
                company_id=person.company_id,
                person_id=person_id,
                profile_id=profile_id,
                action="message1",
                state="done",
            )
            session.add(task)
            session.flush()
            data = {
                "angle": "Enquiry handoff",
                "body": "A useful first-message question.",
                "measurement": {"segment": "HVAC", "warm": True},
            }
            task.approval_digest = fingerprint(data)
            session.add(
                LinkedInDraft(
                    task_id=task.id,
                    revision=1,
                    snapshot=data,
                    digest=task.approval_digest,
                    state="approved",
                    created_by=owner.user.id,
                )
            )
            session.add(
                Touch(
                    event_key="linkedin_sent:" + task.id,
                    company_id=person.company_id,
                    person_id=person.id,
                    profile_id=profile_id,
                    channel="linkedin",
                    direction="outbound",
                    outcome="message1",
                    occurred_at=START + timedelta(days=2),
                    actor_id=owner.user.id,
                )
            )
    queries: list[object] = []

    def counted(*args: object) -> None:
        """Observe SQL count without collecting query contents or parameters."""
        queries.append(None)

    event.listen(db.engine, "before_cursor_execute", counted)
    try:
        result = metrics.report(db, owner.user.id, date(2026, 10, 26))
    finally:
        event.remove(db.engine, "before_cursor_execute", counted)
    assert result["angles"][0]["contacts"] == 50
    assert len(queries) <= 20
