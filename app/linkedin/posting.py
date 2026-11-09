"""Durable one-shot public publishing with no automatic replay of uncertain writes."""

from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.linkedin_post_models import LinkedInPost, PostingConnection, PostMedia
from app.db.session import Database
from app.integrations import linkedin as provider
from app.jobs.queue import enqueue
from app.linkedin.media import bytes_for
from app.linkedin.posts import capacity_valid, valid
from app.linkedin.quotas import lock_policy
from app.linkedin.timing import daytime, local_day
from app.operations.control import maintenance_enabled


def plan(db: Database, settings: Settings, now: datetime) -> None:
    """Recover stale claims into review and queue only due, approved, current-day posts."""
    ready: list[tuple[str, str]] = []
    with db.sessions.begin() as session:
        if maintenance_enabled(session):
            return
        for post in session.scalars(
            select(LinkedInPost).where(
                LinkedInPost.state.in_(
                    ["approved", "uploading", "uploaded", "submitting", "native"]
                )
            )
        ):
            lock_policy(session, post.profile_id, now)
            session.refresh(post)
            if post.state in {"uploading", "submitting", "native"}:
                if post.claimed_at and post.claimed_at < now - timedelta(minutes=15):
                    post.state = "manual" if post.state == "uploading" else "needs_check"
                    post.reason = "Interrupted work; check before any further publication"
                continue
            if post.due_at > now:
                continue
            if local_day(post.due_at) != local_day(now):
                post.state, post.reason = (
                    "review",
                    "Missed scheduled day; prepare a new reviewed slot",
                )
                continue
            if not daytime(now):
                continue
            if (
                post.state == "uploaded"
                and post.claimed_at
                and post.claimed_at > now - timedelta(minutes=2)
            ):
                continue
            if settings.linkedin_posting_enabled:
                ready.append((post.id, post.state))
            else:
                post.state, post.reason = "manual", "Official posting is off; use native posting"
    for post_id, state in ready:
        enqueue(
            db,
            f"li-post:{post_id}:{state}:{int(now.timestamp()) // 60}",
            "linkedin_post",
            {"post_id": post_id},
            now,
        )


def dispatch(db: Database, settings: Settings, post_id: str) -> str:
    """Commit a transmission state before provider calls; every later failure remains visible."""
    now = utc_now()
    with db.sessions.begin() as session:
        post = session.get(LinkedInPost, post_id)
        if not post or not settings.linkedin_posting_enabled or maintenance_enabled(session):
            return "Posting disabled or unavailable."
        lock_policy(session, post.profile_id, now)
        session.refresh(post)
        item = valid(session, post)
        if post.state not in {"approved", "uploaded"}:
            return "Post already claimed or needs review."
        if (
            not item
            or not capacity_valid(session, post, now)
            or not daytime(now)
            or post.due_at > now
            or local_day(post.due_at) != local_day(now)
        ):
            post.state, post.reason = "review", "Approval or scheduled day needs review"
            return post.reason
        connection = session.get(PostingConnection, post.profile_id)
        if (
            not connection
            or connection.state != "active"
            or connection.expires_at <= now
            or (
                connection.subject != item.snapshot["subject"]
                or connection.profile_url != item.snapshot["profile_url"]
            )
        ):
            post.state, post.reason = (
                "manual",
                "Authorize the same member again or use native posting",
            )
            return post.reason
        uploading = post.kind != "text" and post.state == "approved"
        if (
            not uploading
            and post.kind != "text"
            and post.claimed_at
            and (now < post.claimed_at + timedelta(minutes=2))
        ):
            return "Media is processing."
        post.state, post.claimed_at = ("uploading" if uploading else "submitting"), now
        profile_id, author, body, media_urn = (
            post.profile_id,
            "urn:li:person:" + connection.subject,
            item.snapshot["body"],
            post.media_urn,
        )
        kind, data = post.kind, b""
        if uploading:
            media = session.get(PostMedia, item.snapshot["media_id"])
            assert media
            data = bytes_for(session, media)
    state, reason, url, result_urn = "review", "Provider configuration needs review", "", media_urn
    try:
        token = read_secret(db, settings, "linkedin:" + profile_id)
        if uploading:
            result_urn = provider.upload(data, kind, author, token, settings.linkedin_api_version)
            state, reason = "uploaded", "Media uploaded; waiting before publishing"
        else:
            if kind == "video":
                status = provider.api(
                    "GET",
                    "/rest/videos/" + quote(media_urn, safe=""),
                    token,
                    settings.linkedin_api_version,
                ).data.get("status")
                if status != "AVAILABLE":
                    raise provider.LinkedInError()
            url = provider.publish(author, body, media_urn, token, settings.linkedin_api_version)
            state, reason = "published", "Official API confirmed publication"
    except provider.LinkedInError as error:
        state = "manual" if uploading or not error.uncertain else "needs_check"
        reason = (
            "Provider rejected or could not prepare media; use native posting"
            if state == "manual"
            else "Publication result uncertain; check LinkedIn before continuing"
        )
    except ValueError:
        state, reason = "manual", "Posting credentials unavailable; reconnect or use native posting"
    with db.sessions.begin() as session:
        post = session.get(LinkedInPost, post_id)
        assert post
        lock_policy(session, post.profile_id, utc_now())
        session.refresh(post)
        if post.state == ("uploading" if uploading else "submitting"):
            if state == "published":
                post.published_at = utc_now()
            post.state, post.reason, post.published_url, post.media_urn = (
                state,
                reason,
                url,
                result_urn,
            )
    return reason
