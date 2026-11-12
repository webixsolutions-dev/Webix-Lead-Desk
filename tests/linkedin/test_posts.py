"""Exact public post approvals, schedule limits and no uncertain publication replay."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from app.core.config import Settings
from app.core.vault import store_secret
from app.db.linkedin_post_models import LinkedInPost, PostingConnection, PostRevision
from app.db.models import WorkspaceSetting
from app.db.session import Database
from app.integrations import linkedin as provider
from app.linkedin import posting, posts
from app.linkedin.identity import add_profile
from tests.conftest import Account, sign_in

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)
DUE = NOW + timedelta(hours=2)
BODY = (
    "A useful habit for service teams: write down who owns the next customer update. "
    "Clear handoffs make a busy day easier to manage."
)


def test_unassigned_post_copy_is_owner_only_and_needs_no_profile(
    client: TestClient, db: Database, owner: Account, reviewer: Account
) -> None:
    """Unassigned copy is visible for planning without creating a posting slot."""
    with db.sessions.begin() as session:
        session.add(
            WorkspaceSetting(
                key="linkedin_post_copy_drafts",
                value={"drafts": [{"title": "A draft idea", "market": "Calgary", "body": BODY}]},
            )
        )
    sign_in(client, reviewer)
    assert "A draft idea" not in client.get("/linkedin/posts").text
    sign_in(client, owner)
    page = client.get("/linkedin/posts")
    assert page.status_code == 200
    assert "A draft idea · Calgary" in page.text
    assert BODY in page.text
    with db.sessions() as session:
        assert session.scalar(select(LinkedInPost.id)) is None


def prepare(
    db: Database, owner: Account, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> tuple[str, Settings]:
    """Create a synthetic OAuth-connected member and an approved future text post."""
    monkeypatch.setattr(posts, "utc_now", lambda: NOW)
    profile = add_profile(
        db, owner.user.id, "https://linkedin.com/in/test-post-owner", owner.user.id
    )
    config = settings.model_copy(
        update={
            "linkedin_posting_enabled": True,
            "encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    with db.sessions.begin() as session:
        session.add(
            PostingConnection(
                profile_id=profile.id,
                subject="synthetic",
                member_name="Synthetic Member",
                profile_url=profile.profile_url,
                state="active",
                expires_at=DUE + timedelta(days=20),
            )
        )
    store_secret(db, config, "linkedin:" + profile.id, "synthetic-test-only-token", owner.user.id)
    post = posts.create_slot(db, profile.id, owner.user.id, "Team handoffs", "text", DUE)
    item = posts.save(db, post.id, owner.user.id, 0, BODY)
    posts.approve(db, post.id, owner.user.id, item.digest)
    return post.id, config


def test_exact_post_edit_and_immutable_snapshot(
    db: Database, owner: Account, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Changing the draft invalidates approval; database guards prevent rewriting old evidence."""
    post_id, _ = prepare(db, owner, monkeypatch, settings)
    with db.sessions() as session:
        post = session.get(LinkedInPost, post_id)
        assert post and posts.valid(session, post)
    posts.save(db, post_id, owner.user.id, 1, BODY + " Start with one task.")
    with db.sessions() as session:
        post = session.get(LinkedInPost, post_id)
        assert post and not posts.valid(session, post)
    with pytest.raises(DatabaseError), db.sessions.begin() as session:
        session.execute(text("UPDATE linkedin_post_revisions SET digest = 'tamper'"))


@pytest.mark.parametrize("uncertain,expected", [(True, "needs_check"), (False, "manual")])
def test_publication_failures_do_not_replay(
    db: Database,
    owner: Account,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    uncertain: bool,
    expected: str,
) -> None:
    """A timeout needs a human check; a definitive rejection offers native fallback."""
    post_id, config = prepare(db, owner, monkeypatch, settings)
    monkeypatch.setattr(posting, "utc_now", lambda: DUE)
    publish = Mock(side_effect=provider.LinkedInError(uncertain=uncertain))
    monkeypatch.setattr(provider, "publish", publish)
    posting.dispatch(db, config, post_id)
    posting.dispatch(db, config, post_id)
    with db.sessions() as session:
        post = session.get(LinkedInPost, post_id)
        assert post and post.state == expected
    assert publish.call_count == 1


def test_success_does_not_publish_twice_and_state_precedes_http(
    db: Database, owner: Account, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh process sees a durable submitting marker before a mocked provider call."""
    post_id, config = prepare(db, owner, monkeypatch, settings)
    monkeypatch.setattr(posting, "utc_now", lambda: DUE)

    def publish(*args: object) -> str:
        with db.sessions() as session:
            post = session.get(LinkedInPost, post_id)
            assert post and post.state == "submitting"
        return "https://www.linkedin.com/feed/update/urn:li:share:123/"

    mocked = Mock(side_effect=publish)
    monkeypatch.setattr(provider, "publish", mocked)
    posting.dispatch(db, config, post_id)
    posting.dispatch(db, config, post_id)
    assert mocked.call_count == 1
    with db.sessions() as session:
        post = session.get(LinkedInPost, post_id)
        assert post and post.state == "published" and post.published_url


def test_calendar_duplicate_copy_quota_and_video_prompt(
    db: Database,
    owner: Account,
    reviewer: Account,
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    """Distinct profiles cannot reuse captions; warm-up caps and video-only upload stay explicit."""
    post_id, _ = prepare(db, owner, monkeypatch, settings)
    with db.sessions() as session:
        first = session.get(LinkedInPost, post_id)
        assert first
        profile_id = first.profile_id
    with pytest.raises(ValueError, match="limit"):
        posts.create_slot(
            db, profile_id, owner.user.id, "Another topic", "text", DUE + timedelta(hours=1)
        )
    second = add_profile(
        db, reviewer.user.id, "https://linkedin.com/in/test-post-reviewer", owner.user.id
    )
    post = posts.create_slot(
        db, second.id, owner.user.id, "A different topic", "text", DUE + timedelta(hours=1)
    )
    with pytest.raises(ValueError, match="already used"):
        posts.save(db, post.id, owner.user.id, 0, BODY)
    video = posts.create_slot(
        db, second.id, owner.user.id, "Video lesson", "video", DUE + timedelta(days=1)
    )
    assert posts.static_prompt(second, video) == ""
    with pytest.raises(ValueError, match="limit"):
        posts.create_slot(
            db, second.id, owner.user.id, "Third topic", "text", DUE + timedelta(days=2)
        )


def test_native_is_scoped_and_requires_exact_approval(
    db: Database,
    owner: Account,
    reviewer: Account,
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    """Manual fallback cannot bypass member identity, exact copy or the scheduled day."""
    post_id, _ = prepare(db, owner, monkeypatch, settings)
    with pytest.raises(ValueError):
        posts.native_action(db, post_id, reviewer.user.id, "start")
    with pytest.raises(ValueError, match="scheduled day"):
        posts.native_action(db, post_id, owner.user.id, "start")
    monkeypatch.setattr(posts, "utc_now", lambda: DUE)
    posts.native_action(db, post_id, owner.user.id, "start")
    with pytest.raises(ValueError):
        posts.save(db, post_id, owner.user.id, 1, BODY + " More detail.")
    posts.native_action(
        db,
        post_id,
        owner.user.id,
        "published",
        "https://www.linkedin.com/feed/update/urn:li:activity:123/",
    )
    with db.sessions() as session:
        assert session.scalar(select(PostRevision))


def test_expired_token_and_crash_recovery(
    db: Database, owner: Account, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expiry offers native fallback; an abandoned transmission becomes needs-check."""
    post_id, config = prepare(db, owner, monkeypatch, settings)
    monkeypatch.setattr(posting, "utc_now", lambda: DUE)
    with db.sessions.begin() as session:
        connection = session.scalar(select(PostingConnection))
        assert connection
        connection.expires_at = NOW
    posting.dispatch(db, config, post_id)
    with db.sessions.begin() as session:
        post = session.get(LinkedInPost, post_id)
        assert post and post.state == "manual"
        post.state, post.claimed_at = "submitting", DUE - timedelta(minutes=20)
    posting.plan(db, config, DUE)
    with db.sessions() as session:
        post = session.get(LinkedInPost, post_id)
        assert post and post.state == "needs_check"
