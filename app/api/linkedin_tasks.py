"""Owner draft review and member-scoped manual action cards."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.core.timezones import zone
from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Person
from app.db.linkedin_queue_models import LinkedInSequence
from app.db.models import new_id
from app.db.outreach_models import Mailbox
from app.db.research_models import Evidence
from app.jobs.queue import enqueue
from app.linkedin.actions import complete, person_event
from app.linkedin.board import planned_tasks
from app.linkedin.drafts import approve, latest, save, valid
from app.linkedin.leases import claim_task, live_claim
from app.linkedin.timing import daytime, local_day

router = APIRouter()


@router.get("/linkedin/queue", response_class=HTMLResponse)
def queue(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show only the signed-in member's approved queue, or all owner review tasks."""
    context = dashboard_context(
        request, principal, "outbox" if principal.user.role == "owner" else "linkedin"
    )
    context["planned_tasks"] = planned_tasks(services(request).db, principal.user, utc_now())
    with services(request).db.sessions() as session:
        query = (
            select(Person, LinkedInSequence)
            .join(LinkedInSequence, LinkedInSequence.person_id == Person.id)
            .join(LinkedInProfile, LinkedInProfile.id == LinkedInSequence.profile_id)
            .where(
                LinkedInSequence.state.in_(["invited", "review", "closed", "withdrawn", "replied"])
            )
        )
        if principal.user.role != "owner":
            query = query.where(LinkedInProfile.user_id == principal.user.id)
        context["connections"] = list(session.execute(query.limit(100)))
        context["connection_tasks"] = {
            person.id: session.scalar(
                select(LinkedInTask.id)
                .where(LinkedInTask.person_id == person.id)
                .order_by(LinkedInTask.created_at.desc())
                .limit(1)
            )
            for person, _ in context["connections"]
        }
    return render(request, "linkedin_queue.html", context)


@router.get("/linkedin/tasks/{task_id}", response_class=HTMLResponse)
def task_page(request: Request, principal: CurrentUser, task_id: str) -> HTMLResponse:
    """Owners review copy; members open only their own approved human-action card."""
    context = dashboard_context(
        request, principal, "outbox" if principal.user.role == "owner" else "linkedin"
    )
    with services(request).db.sessions() as session:
        task = session.get(LinkedInTask, task_id)
        profile = session.get(LinkedInProfile, task.profile_id) if task else None
        if (
            not task
            or not profile
            or (
                principal.user.role != "owner"
                and (
                    profile.user_id != principal.user.id
                    or task.state not in {"ready", "acting", "needs_check", "done", "cancelled"}
                )
            )
        ):
            raise HTTPException(404, "Task not found")
        draft = latest(session, task.id)
        if principal.user.role != "owner" and (not draft or draft.state != "approved"):
            raise HTTPException(404, "Approved task not found")
        context.update(
            task=task,
            profile=profile,
            person=session.get(Person, task.person_id),
            draft=draft,
            is_member=profile.user_id == principal.user.id,
            can_start=bool(
                task.release_at
                and task.release_at <= utc_now()
                and task.planned_day == local_day(utc_now())
                and daytime(utc_now())
                and valid(session, task)
            ),
            release_label=task.release_at.astimezone(zone("America/Edmonton")).strftime(
                "%a %d %b, %H:%M Calgary"
            )
            if task.release_at
            else "Waiting for the next plan",
            claim_live=bool(
                task.lease_token
                and live_claim(task, task.lease_token, utc_now())
                and valid(session, task)
            ),
            mailboxes=list(session.scalars(select(Mailbox)))
            if principal.user.role == "owner"
            else [],
            evidence=list(
                session.scalars(
                    select(Evidence).where(
                        Evidence.company_id == task.company_id, Evidence.reviewed_by.is_not(None)
                    )
                )
            )
            if principal.user.role == "owner"
            else [],
            request_key=new_id(),
            ai_enabled=services(request).settings.openai_enabled,
        )
    if principal.user.role == "owner":
        from app.linkedin.brief import asset_prompt

        context["asset_prompt"] = asset_prompt(services(request).db, task.company_id)
    return render(request, "linkedin_task.html", context)


@router.post("/linkedin/tasks/{task_id}/draft")
async def save_copy(request: Request, principal: Owner, task_id: str) -> RedirectResponse:
    """Store a fresh bounded content revision, never an implicit approval."""
    fields = await read_form(request, maximum=24000, repeatable=frozenset({"evidence_ids"}))
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        expiry = (
            datetime.fromisoformat(fields["basis_expiry"])
            .replace(tzinfo=zone("America/Edmonton"))
            .isoformat()
            if fields.get("basis_expiry")
            else ""
        )
        save(
            services(request).db,
            task_id,
            principal.user.id,
            fields.get("body", ""),
            mailbox_id=fields.get("mailbox_id", ""),
            evidence_ids=[
                x.strip() for x in fields.get("evidence_ids", "").split(",") if x.strip()
            ],
            basis={
                "kind": fields.get("basis_kind", ""),
                "note": fields.get("basis_note", ""),
                "source": fields.get("basis_source", ""),
                "expires_at": expiry,
            },
            angle=fields.get("angle", ""),
            noncommercial=fields.get("noncommercial") == "true",
            previous_id=fields.get("previous_id") or None,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/tasks/" + task_id, 303)


@router.post("/linkedin/tasks/{task_id}/approve")
async def approve_copy(request: Request, principal: Owner, task_id: str) -> RedirectResponse:
    """Bind the approval button to the current exact revision of this task."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        with services(request).db.sessions() as session:
            draft = latest(session, task_id)
            if not draft or draft.id != fields.get("draft_id"):
                raise ValueError("Draft changed; reload")
        approve(services(request).db, draft.id, fields.get("digest", ""), principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/tasks/" + task_id, 303)


@router.post("/linkedin/tasks/{task_id}/start")
async def start(request: Request, principal: CurrentUser, task_id: str) -> RedirectResponse:
    """Reserve the current member's one human action within all active fences."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    now = utc_now()
    if not claim_task(
        services(request).db,
        task_id,
        principal.user.id,
        now,
        approved=lambda session, task: valid(session, task, now),
    ):
        raise HTTPException(
            409, "This action is not ready. Check its approval, time and daily limits."
        )
    return RedirectResponse("/linkedin/tasks/" + task_id, 303)


@router.post("/linkedin/tasks/{task_id}/complete")
async def completed(request: Request, principal: CurrentUser, task_id: str) -> RedirectResponse:
    """Record the member's explicit confirmation; this endpoint sends nothing."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("confirmed") != "true":
        raise HTTPException(422, "Confirm the actual LinkedIn action completed")
    try:
        complete(
            services(request).db, task_id, principal.user.id, fields.get("token", ""), utc_now()
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return RedirectResponse("/linkedin/queue", 303)


@router.post("/people/{person_id}/event")
async def observation(request: Request, principal: CurrentUser, person_id: str) -> RedirectResponse:
    """Record acceptance or identity correction without sending a message."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        person_event(
            services(request).db, person_id, principal.user.id, fields.get("event", ""), utc_now()
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/queue", 303)


@router.post("/linkedin/tasks/{task_id}/suggest")
async def suggest(request: Request, principal: Owner, task_id: str) -> RedirectResponse:
    """Queue optional copy improvement of a stored unapproved brief."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    with services(request).db.sessions() as session:
        draft = latest(session, task_id)
        if not draft or draft.state != "draft":
            raise HTTPException(422, "Save an unapproved brief first")
    enqueue(
        services(request).db,
        "linkedin_copy:" + draft.id,
        "linkedin_draft",
        {"draft_id": draft.id},
        utc_now(),
    )
    return RedirectResponse("/linkedin/tasks/" + task_id, 303)
