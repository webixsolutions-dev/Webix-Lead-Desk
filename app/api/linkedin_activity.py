"""Named LinkedIn conversations and factual daily counts from recorded human actions."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from starlette.responses import HTMLResponse

from app.api.dependencies import Owner, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.core.timezones import zone
from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Person, PersonOwnership, Touch
from app.db.linkedin_queue_models import LinkedInSequence
from app.db.models import new_id
from app.drafting.context import linkedin_context

router = APIRouter()


@router.get("/linkedin/activity", response_class=HTMLResponse)
def activity(request: Request, principal: Owner) -> HTMLResponse:
    """Count completed actions, not queued invitations, in the display timezone."""
    context = dashboard_context(request, principal, "linkedin")
    start = (
        utc_now()
        .astimezone(zone(context["preferences"].display_timezone))
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )
    end = start + timedelta(days=1)
    with services(request).db.sessions() as session:
        profiles = list(session.scalars(select(LinkedInProfile)))
        counts = []
        for profile in profiles:
            counts.append(
                {
                    "profile": profile,
                    "sent": session.scalar(
                        select(func.count())
                        .select_from(LinkedInTask)
                        .where(
                            LinkedInTask.profile_id == profile.id,
                            LinkedInTask.action == "invite",
                            LinkedInTask.state == "done",
                            LinkedInTask.completed_at >= start,
                            LinkedInTask.completed_at < end,
                        )
                    )
                    or 0,
                    "accepted": session.scalar(
                        select(func.count())
                        .select_from(LinkedInSequence)
                        .where(
                            LinkedInSequence.profile_id == profile.id,
                            LinkedInSequence.accepted_at >= start,
                            LinkedInSequence.accepted_at < end,
                        )
                    )
                    or 0,
                    "replies": session.scalar(
                        select(func.count())
                        .select_from(Touch)
                        .where(
                            Touch.profile_id == profile.id,
                            Touch.channel == "linkedin",
                            Touch.direction == "inbound",
                            Touch.occurred_at >= start,
                            Touch.occurred_at < end,
                        )
                    )
                    or 0,
                }
            )
        people = list(
            session.scalars(
                select(Person).join(PersonOwnership).order_by(Person.full_name).limit(200)
            )
        )
        context.update(
            counts=counts,
            people=people,
            sequences={p.id: session.get(LinkedInSequence, p.id) for p in people},
            latest_replies={
                p.id: session.scalar(
                    select(Touch)
                    .where(
                        Touch.person_id == p.id,
                        Touch.channel == "linkedin",
                        Touch.direction == "inbound",
                    )
                    .order_by(Touch.occurred_at.desc())
                    .limit(1)
                )
                for p in people
            },
        )
    return render(request, "linkedin_activity.html", context)


@router.get("/linkedin/conversations/{person_id}", response_class=HTMLResponse)
def conversation(request: Request, principal: Owner, person_id: str) -> HTMLResponse:
    """Show one recipient's recorded chat, acceptance and pending reply draft tasks."""
    context = dashboard_context(request, principal, "linkedin")
    with services(request).db.sessions() as session:
        person = session.get(Person, person_id)
        ownership = session.get(PersonOwnership, person_id)
        if not person or not ownership:
            raise HTTPException(404, "Assigned person not found")
        context.update(
            person=person,
            thread=linkedin_context(session, person_id, ownership.profile_id),
            sequence=session.get(LinkedInSequence, person_id),
            request_key=new_id(),
            tasks=list(
                session.scalars(
                    select(LinkedInTask)
                    .where(LinkedInTask.person_id == person_id)
                    .order_by(LinkedInTask.created_at.desc())
                    .limit(20)
                )
            ),
        )
    return render(request, "linkedin_conversation.html", context)
