"""Observed cohorts, sample thresholds, scoped snapshots and owner-reviewed learning."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DatabaseError

from app.core.config import Settings
from app.db.learning_models import LearningProposal
from app.db.linkedin_draft_models import LinkedInDraft
from app.db.linkedin_metric_models import PostObservation
from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Person, Touch
from app.db.linkedin_post_models import LinkedInPost
from app.db.models import Job, new_id
from app.db.outreach_models import PlaybookVersion
from app.db.session import Database
from app.drafting.content import fingerprint
from app.jobs.queue import enqueue_health
from app.jobs.runner import run_worker
from app.learning.service import apply_proposal
from app.linkedin import learning, metrics
from app.linkedin.identity import add_profile
from app.linkedin.metric_review import PostCounts, record_post, record_profile, resume
from app.operations.control import set_maintenance
from tests.acquisition.helpers import business
from tests.conftest import Account, csrf_from, sign_in

START = datetime(2026, 10, 27, 16, tzinfo=UTC)
NOW = datetime(2026, 11, 9, 18, tzinfo=UTC)


def cohort(
    db: Database, owner: Account, *, count: int = 50, accepted: int = 9
) -> tuple[str, list[str]]:
    """Create synthetic immutable observed actions; no platform or prospect is contacted."""
    company, _ = business(db, owner)
    profile = add_profile(db, owner.user.id, "https://linkedin.com/in/metrics-owner", owner.user.id)
    people = []
    with db.sessions.begin() as session:
        for number in range(count):
            person = Person(
                company_id=company.id,
                full_name=f"Test Person {number}",
                role="Owner",
                source_url="https://example.test/team",
                verification_note="Synthetic identity fixture only",
                verified_by=owner.user.id,
            )
            session.add(person)
            session.flush()
            people.append(person.id)
            session.add(
                Touch(
                    event_key="fixture:invite:" + person.id,
                    company_id=company.id,
                    person_id=person.id,
                    profile_id=profile.id,
                    channel="linkedin",
                    direction="outbound",
                    outcome="invite",
                    occurred_at=START,
                    actor_id=owner.user.id,
                )
            )
            if number < accepted:
                session.add(
                    Touch(
                        event_key="fixture:accept:" + person.id,
                        company_id=company.id,
                        person_id=person.id,
                        profile_id=profile.id,
                        channel="linkedin",
                        direction="observation",
                        outcome="accepted",
                        occurred_at=START + timedelta(days=1),
                        actor_id=owner.user.id,
                    )
                )
    return profile.id, people


@pytest.mark.parametrize("count,accepted,paused", [(49, 0, False), (50, 9, True), (50, 10, False)])
def test_acceptance_pause_exact_threshold_and_resume(
    db: Database, owner: Account, count: int, accepted: int, paused: bool
) -> None:
    """Enforce the sample/rate boundary and preserve history after owner resume."""
    profile_id, _ = cohort(db, owner, count=count, accepted=accepted)
    with db.sessions.begin() as session:
        assert learning.evaluate_pause(session, profile_id, NOW) == paused
    with db.sessions() as session:
        profile = session.get(LinkedInProfile, profile_id)
        assert profile and profile.status == ("paused" if paused else "active")
    if paused:
        resume(
            db,
            profile_id,
            owner.user.id,
            "Reviewed narrower targeting and profile positioning.",
            NOW,
        )
        with db.sessions.begin() as session:
            assert not learning.evaluate_pause(session, profile_id, NOW + timedelta(seconds=1))
            assert session.scalar(select(func.count()).select_from(Touch)) == count + accepted


def test_weekly_cohort_dedup_reply_time_and_scope(
    db: Database, owner: Account, reviewer: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate events do not inflate people; earlier/unrelated replies cannot convert a cohort."""
    profile_id, people = cohort(db, owner, count=3, accepted=1)
    monkeypatch.setattr(metrics, "utc_now", lambda: NOW)
    with db.sessions.begin() as session:
        first = session.get(Person, people[0])
        assert first
        for key, direction, outcome, when in [
            ("same-invite", "outbound", "invite", START),
            ("m1", "outbound", "message1", START + timedelta(days=1)),
            ("earlier", "inbound", "interested", START - timedelta(hours=1)),
            ("reply", "inbound", "not_now", START + timedelta(days=2)),
            ("repeat-reply", "inbound", "not_now", START + timedelta(days=2)),
        ]:
            session.add(
                Touch(
                    event_key=key,
                    company_id=first.company_id,
                    person_id=first.id,
                    profile_id=profile_id,
                    channel="linkedin",
                    direction=direction,
                    outcome=outcome,
                    occurred_at=when,
                    actor_id=owner.user.id,
                )
            )
    row = metrics.report(db, owner.user.id, date(2026, 10, 26))["funnels"][0]
    assert (row["invites"], row["accepted"], row["contacted"], row["replied"], row["positive"]) == (
        3,
        1,
        1,
        1,
        0,
    )
    assert metrics.report(db, reviewer.user.id, date(2026, 10, 26))["funnels"] == []


def test_angle_exact_provenance_dimensions_and_earliest_attribution(
    db: Database, owner: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attribute each person once to their first exact first-message angle."""
    profile_id, people = cohort(db, owner, count=1, accepted=1)
    monkeypatch.setattr(metrics, "utc_now", lambda: NOW)
    with db.sessions.begin() as session:
        person = session.get(Person, people[0])
        assert person
        for number, angle in enumerate(["Clear next step", "Later angle"]):
            task = LinkedInTask(
                key=new_id(),
                company_id=person.company_id,
                person_id=person.id,
                profile_id=profile_id,
                action="message1",
                state="done",
                generation=number + 1,
            )
            session.add(task)
            session.flush()
            snapshot: dict[str, Any] = {
                "body": "A short question about your enquiry handoff.",
                "angle": angle,
                "measurement": {"segment": "HVAC", "warm": True},
            }
            task.approval_digest = fingerprint(snapshot)
            session.add(
                LinkedInDraft(
                    task_id=task.id,
                    revision=1,
                    snapshot=snapshot,
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
                    occurred_at=START + timedelta(days=2 + number),
                    actor_id=owner.user.id,
                )
            )
        session.add(
            Touch(
                event_key="positive",
                company_id=person.company_id,
                person_id=person.id,
                profile_id=profile_id,
                channel="linkedin",
                direction="inbound",
                outcome="interested",
                occurred_at=START + timedelta(days=4),
                actor_id=owner.user.id,
            )
        )
    result = metrics.report(db, owner.user.id, date(2026, 10, 26))
    assert len(result["angles"]) == 1
    row = result["angles"][0]
    assert (row["angle"], row["contacts"], row["positive"], row["warm"]) == (
        "Clear next step",
        1,
        1,
        "warm",
    )
    assert row["lengths"] == {"≤200 chars"} and result["winners"] == []


def test_no_winner_below_50_per_variant_or_tied_or_different_context() -> None:
    """A tiny high-response sample cannot beat a larger sample or unrelated segment."""
    a = {"angle": "A", "segment": "HVAC", "warm": "cold", "contacts": 50, "positive": 10}
    b = {**a, "angle": "B", "contacts": 49, "positive": 1}
    assert metrics.candidate_winners([a, b]) == []
    assert metrics.candidate_winners([a, {**b, "contacts": 50}]) == [a]
    assert metrics.candidate_winners([a, {**b, "contacts": 50, "positive": 10}]) == []
    assert metrics.candidate_winners([a, {**b, "contacts": 50, "hours": {"11:00"}}]) == []


def test_fortnight_proposal_is_inert_idempotent_and_owner_applied(
    db: Database, owner: Account
) -> None:
    """Propose a completed period once; owner application alone changes guidance."""
    cohort(db, owner, count=2, accepted=1)
    learning.propose(db, NOW)
    learning.propose(db, NOW)
    with db.sessions() as session:
        items = list(session.scalars(select(LearningProposal)))
        assert len(items) == 1 and items[0].state == "pending"
        assert (
            session.scalar(select(PlaybookVersion).where(PlaybookVersion.scope == "global")) is None
        )
        proposal_id = items[0].id
    version = apply_proposal(
        db,
        proposal_id,
        "For LinkedIn only: keep questions specific to a reviewed enquiry-handling observation.",
        0,
        owner.user.id,
    )
    assert version.version == 1


def test_manual_metrics_scope_latest_snapshot_and_immutable(
    db: Database, owner: Account, reviewer: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post observations are cumulative snapshots, not additive totals, and remain auditable."""
    profile_id, _ = cohort(db, owner, count=1, accepted=1)
    monkeypatch.setattr(metrics, "utc_now", lambda: NOW)
    with db.sessions.begin() as session:
        post = LinkedInPost(
            profile_id=profile_id,
            topic="Useful handoffs",
            kind="text",
            due_at=START,
            state="published",
            published_at=START,
            published_url="https://www.linkedin.com/feed/update/urn:li:share:123/",
        )
        session.add(post)
        session.flush()
        post_id = post.id
    counts = PostCounts(
        impressions=100,
        reactions=5,
        comments=1,
        profile_views=3,
        target_connections=1,
        target_dms=1,
    )
    with pytest.raises(ValueError):
        record_post(
            db, post_id, reviewer.user.id, new_id(), counts, "Observed on member analytics."
        )
    record_post(db, post_id, owner.user.id, new_id(), counts, "Observed on member analytics.")
    second = counts.model_copy(update={"impressions": 200, "target_dms": 2})
    record_post(
        db, post_id, owner.user.id, new_id(), second, "Second cumulative analytics observation."
    )
    result = metrics.report(db, owner.user.id, date(2026, 10, 26))
    assert result["observations"][post_id].counts["impressions"] == 200
    assert result["published_this_week"] == 1
    with pytest.raises(DatabaseError), db.sessions.begin() as session:
        session.execute(text("UPDATE linkedin_post_observations SET note = 'changed'"))
    record_profile(
        db, profile_id, owner.user.id, new_id(), 55, "Synthetic restriction notice observed.", True
    )
    with db.sessions() as session:
        profile = session.get(LinkedInProfile, profile_id)
        assert profile and profile.status == "restricted"
        assert len(list(session.scalars(select(PostObservation)))) == 2


def test_results_routes_and_maintenance(
    db: Database,
    client: TestClient,
    owner: Account,
    reviewer: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reviewer URLs remain scoped, writes need CSRF, and maintenance does not create proposals."""
    profile_id, _ = cohort(db, owner, count=2, accepted=1)
    monkeypatch.setattr(metrics, "utc_now", lambda: NOW)
    sign_in(client, owner)
    page = client.get("/linkedin/results?week=2026-10-26")
    assert page.status_code == 200 and "Weekly member funnel" in page.text
    assert (
        client.post(f"/linkedin/results/profiles/{profile_id}", data={"ssi": "50"}).status_code
        == 403
    )
    sign_in(client, reviewer)
    page = client.get("/linkedin/results?week=2026-10-26")
    assert "metrics-owner" not in page.text and "No member data yet" in page.text
    assert (
        client.post(
            f"/linkedin/results/profiles/{profile_id}",
            data={"csrf_token": csrf_from(page.text), "key": new_id(), "ssi": "50"},
        ).status_code
        == 422
    )
    set_maintenance(db, True, "Synthetic maintenance before reporting")
    learning.tick(db, NOW)
    with db.sessions() as session:
        assert session.scalar(select(LearningProposal)) is None


def test_worker_once_runs_observation_tick_without_provider_calls(
    db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real worker loop evaluates reporting and still completes its health job offline."""
    observed = Mock(wraps=learning.tick)
    monkeypatch.setattr(learning, "tick", observed)
    from app.core.clock import utc_now

    enqueue_health(db, "metrics-worker-health", utc_now())
    run_worker(db, settings.model_copy(update={"pc_keep_awake": False}), once=True)
    assert observed.call_count == 1
    with db.sessions() as session:
        job = session.scalar(select(Job).where(Job.unique_key == "metrics-worker-health"))
        assert job and job.state == "succeeded"
