"""Observed weekly cohorts and attributed angles; no inferred revenue or platform scraping."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.core.clock import utc_now
from app.db.linkedin_draft_models import LinkedInDraft
from app.db.linkedin_metric_models import PostObservation, ProfileObservation, ProfileRiskReview
from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Touch
from app.db.linkedin_post_models import LinkedInPost
from app.db.linkedin_queue_models import LinkedInSequence
from app.db.session import Database
from app.linkedin.identity import require_staff
from app.sending.window import CALGARY

POSITIVE = {"interested", "meeting_booked"}


def first_touches(
    rows: list[Touch], outcome: str, start: datetime, end: datetime
) -> dict[str, datetime]:
    """Count each observed person once in the cohort, excluding plans and quota reservations."""
    result: dict[str, datetime] = {}
    for row in sorted(rows, key=lambda r: r.occurred_at):
        if (
            row.person_id
            and row.direction == "outbound"
            and row.outcome == outcome
            and (start <= row.occurred_at < end)
        ):
            result.setdefault(row.person_id, row.occurred_at)
    return result


def conversions(
    rows: list[Touch], cohort: dict[str, datetime], kind: str, *, horizon_days: int | None = None
) -> set[str]:
    """Attribute outcomes only after a real contact and optionally within a response horizon."""
    result: set[str] = set()
    for row in rows:
        at = cohort.get(row.person_id or "")
        if (
            not at
            or row.occurred_at < at
            or (horizon_days is not None and row.occurred_at > at + timedelta(days=horizon_days))
        ):
            continue
        matched = (
            kind == "accepted" and row.direction == "observation" and row.outcome == "accepted"
        ) or (
            row.direction == "inbound"
            and (
                kind == "reply"
                or (kind == "positive" and row.outcome in POSITIVE)
                or (kind == "meeting" and row.outcome == "meeting_booked")
            )
        )
        if matched and row.person_id:
            result.add(row.person_id)
    return result


def percentage(numerator: int, denominator: int) -> float | None:
    """Keep missing denominators visibly unknown rather than claiming zero performance."""
    return round(numerator / denominator * 100, 1) if denominator else None


def funnel(
    session: Session, profile: LinkedInProfile, rows: list[Touch], start: datetime, end: datetime
) -> dict[str, Any]:
    """Report invitation and first-message cohorts separately, with current pending counts."""
    invites = first_touches(rows, "invite", start, end)
    contacted = first_touches(rows, "message1", start, end)
    accepted = len(conversions(rows, invites, "accepted"))
    replied = len(conversions(rows, contacted, "reply", horizon_days=30))
    positive = len(conversions(rows, contacted, "positive", horizon_days=30))
    meeting = len(conversions(rows, contacted, "meeting", horizon_days=30))
    observation = session.scalar(
        select(ProfileObservation)
        .where(ProfileObservation.profile_id == profile.id)
        .order_by(ProfileObservation.observed_at.desc())
        .limit(1)
    )
    pending = len(
        list(
            session.scalars(
                select(LinkedInSequence.person_id).where(
                    LinkedInSequence.profile_id == profile.id, LinkedInSequence.state == "invited"
                )
            )
        )
    )
    review = session.get(ProfileRiskReview, profile.id)
    return {
        "profile": profile,
        "invites": len(invites),
        "accepted": accepted,
        "acceptance": percentage(accepted, len(invites)),
        "contacted": len(contacted),
        "replied": replied,
        "reply_rate": percentage(replied, len(contacted)),
        "positive": positive,
        "positive_rate": percentage(positive, len(contacted)),
        "meetings": meeting,
        "pending": pending,
        "observation": observation,
        "risk": review,
    }


def _contexts(
    session: Session, rows: list[Touch]
) -> tuple[
    dict[str, tuple[LinkedInTask, LinkedInDraft]], dict[tuple[str, int, str], LinkedInDraft]
]:
    """Read exact task/draft pairs in bounded batches instead of one query per contact."""
    ids = list(
        {
            r.event_key.removeprefix("linkedin_sent:")
            for r in rows
            if r.direction == "outbound"
            and r.outcome == "message1"
            and r.event_key.startswith("linkedin_sent:")
        }
    )
    tasks: dict[str, tuple[LinkedInTask, LinkedInDraft]] = {}
    base = select(LinkedInTask, LinkedInDraft).join(
        LinkedInDraft,
        (LinkedInDraft.task_id == LinkedInTask.id)
        & (LinkedInDraft.digest == LinkedInTask.approval_digest),
    )
    for offset in range(0, len(ids), 500):
        for task, draft in session.execute(
            base.where(LinkedInTask.id.in_(ids[offset : offset + 500]))
        ):
            tasks[task.id] = (task, draft)
    people = list({task.person_id for task, _ in tasks.values()})
    invites: dict[tuple[str, int, str], LinkedInDraft] = {}
    for offset in range(0, len(people), 500):
        for task, draft in session.execute(
            base.where(
                LinkedInTask.person_id.in_(people[offset : offset + 500]),
                LinkedInTask.action == "invite",
                LinkedInTask.state == "done",
            )
        ):
            invites[(task.person_id, task.generation, task.profile_id)] = draft
    return tasks, invites


def angle_rows(session: Session, rows: list[Touch]) -> list[dict[str, Any]]:
    """Compare first-message variants by distinct contacts; retain original snapshot dimensions."""
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    seen_people: set[str] = set()
    tasks, invites = _contexts(session, rows)
    # Only confirmed first messages with exact task provenance enter this comparison.
    for touch in sorted(rows, key=lambda r: r.occurred_at):
        if (
            touch.direction != "outbound"
            or touch.outcome != "message1"
            or not touch.person_id
            or (not touch.event_key.startswith("linkedin_sent:"))
        ):
            continue
        if touch.person_id in seen_people:
            continue
        task_id = touch.event_key.removeprefix("linkedin_sent:")
        pair = tasks.get(task_id)
        if pair is None:
            continue
        task, draft = pair
        if (
            not task
            or not draft
            or task.person_id != touch.person_id
            or task.profile_id != touch.profile_id
        ):
            continue
        seen_people.add(touch.person_id)
        data = draft.snapshot
        measurement = data.get("measurement", {})
        segment = str(measurement.get("segment", "unknown"))
        warm = {True: "warm", False: "cold"}.get(measurement.get("warm"), "unknown")
        length = len(str(data.get("body", "")))
        length_label = "≤200 chars" if length <= 200 else "201+ chars"
        hour = touch.occurred_at.astimezone(CALGARY).strftime("%H:00")
        note = invites.get((task.person_id, task.generation, task.profile_id))
        note_label = (
            "note"
            if note and note.snapshot.get("body")
            else "blank"
            if note
            else "not applicable (warm)"
            if warm == "warm"
            else "unknown"
        )
        key = (str(data.get("angle", "Unlabelled")), segment, warm, length_label, note_label, hour)
        group = groups.setdefault(
            key,
            {
                "angle": key[0],
                "segment": segment,
                "warm": warm,
                "cohort": {},
                "profiles": {},
                "lengths": {length_label},
                "hours": {hour},
                "notes": {note_label},
            },
        )
        group["cohort"][touch.person_id] = touch.occurred_at
        group["profiles"][touch.person_id] = touch.profile_id
    result = []
    for group in groups.values():
        cohort = group.pop("cohort")
        profiles = group.pop("profiles")
        attributed = [
            r for r in rows if r.person_id in profiles and r.profile_id == profiles[r.person_id]
        ]
        count = len(cohort)
        replied = len(conversions(attributed, cohort, "reply", horizon_days=30))
        positive = len(conversions(attributed, cohort, "positive", horizon_days=30))
        result.append(
            {
                **group,
                "contacts": count,
                "replied": replied,
                "positive": positive,
                "reply_rate": percentage(replied, count),
                "positive_rate": percentage(positive, count),
                "eligible": count >= 50,
            }
        )
    return sorted(result, key=lambda row: (row["segment"], row["warm"], row["angle"]))


def candidate_winners(angles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nominate only unique observed leaders when every variant in a segment has 50+ contacts."""
    comparisons: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in angles:
        context = (
            row["segment"],
            row["warm"],
            ",".join(sorted(row.get("lengths", []))),
            ",".join(sorted(row.get("notes", []))),
            ",".join(sorted(row.get("hours", []))),
        )
        if any("unknown" in value for value in context):
            continue
        comparisons[context].append(row)
    result = []
    for variants in comparisons.values():
        if len(variants) < 2 or any(v["contacts"] < 50 for v in variants):
            continue
        ranked = sorted(variants, key=lambda v: v["positive"] / v["contacts"], reverse=True)
        if (
            ranked[0]["positive"] / ranked[0]["contacts"]
            > ranked[1]["positive"] / ranked[1]["contacts"]
        ):
            result.append(ranked[0])
    return result


def report(db: Database, actor: str, monday: date) -> dict[str, Any]:
    """Return an owner-wide or strictly member-scoped weekly report with explicit denominators."""
    now = utc_now()
    if monday.weekday() != 0 or monday > now.astimezone(CALGARY).date():
        raise ValueError("Choose the start of an existing Calgary week")
    start = datetime.combine(monday, time.min, CALGARY)
    end = start + timedelta(days=7)
    with db.sessions() as session:
        user = require_staff(session, actor)
        query = select(LinkedInProfile)
        if user.role != "owner":
            query = query.where(LinkedInProfile.user_id == actor)
        profiles = list(session.scalars(query.order_by(LinkedInProfile.created_at)))
        ids = [p.id for p in profiles]
        rows = list(
            session.scalars(
                select(Touch)
                .options(defer(Touch.text))
                .where(
                    Touch.channel == "linkedin", Touch.profile_id.in_(ids), Touch.occurred_at <= now
                )
            )
        )
        by_profile = {p.id: [r for r in rows if r.profile_id == p.id] for p in profiles}
        post_rows = list(
            session.scalars(
                select(LinkedInPost)
                .where(LinkedInPost.profile_id.in_(ids), LinkedInPost.state == "published")
                .order_by(LinkedInPost.published_at.desc())
                .limit(100)
            )
        )
        observations = {
            p.id: session.scalar(
                select(PostObservation)
                .where(PostObservation.post_id == p.id)
                .order_by(PostObservation.observed_at.desc())
                .limit(1)
            )
            for p in post_rows
        }
        angles = angle_rows(session, rows)
        return {
            "start": start,
            "end": end,
            "as_of": now,
            "funnels": [funnel(session, p, by_profile[p.id], start, end) for p in profiles],
            "angles": angles,
            "winners": candidate_winners(angles),
            "posts": post_rows,
            "observations": observations,
            "names": {p.id: p.display_name for p in profiles},
            "published_this_week": session.scalar(
                select(func.count())
                .select_from(LinkedInPost)
                .where(
                    LinkedInPost.profile_id.in_(ids),
                    LinkedInPost.state == "published",
                    LinkedInPost.published_at >= start,
                    LinkedInPost.published_at < end,
                )
            )
            or 0,
        }
