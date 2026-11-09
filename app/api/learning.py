"""Private suggestion review and append-only Playbook restoration."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.db.lead_models import Campaign
from app.db.learning_models import LearningProposal
from app.db.outreach_models import PlaybookVersion
from app.learning.service import apply_proposal, dismiss_proposal, rollback

router = APIRouter()


def learning_data(request: Request) -> dict[str, Any]:
    """Read data shared by the settings section and legacy direct route."""
    context: dict[str, Any] = {}
    with services(request).db.sessions() as session:
        latest = (
            select(PlaybookVersion.scope, func.max(PlaybookVersion.version).label("version"))
            .group_by(PlaybookVersion.scope)
            .subquery()
        )
        current = {
            item.scope: item
            for item in session.scalars(
                select(PlaybookVersion).join(
                    latest,
                    (PlaybookVersion.scope == latest.c.scope)
                    & (PlaybookVersion.version == latest.c.version),
                )
            )
        }
        history = list(
            session.scalars(
                select(PlaybookVersion).order_by(PlaybookVersion.created_at.desc()).limit(100)
            )
        )
        context.update(
            proposals=list(
                session.scalars(
                    select(LearningProposal).order_by(LearningProposal.created_at.desc()).limit(100)
                )
            ),
            current_guidance=current,
            history=history[:100],
            scope_names={
                "global": "Global guidance",
                "partner": "Partner guidance",
                **{item.id: item.name for item in session.scalars(select(Campaign))},
            },
        )
    return context


@router.get("/learning", response_class=HTMLResponse)
def learning_page(request: Request, principal: Owner) -> HTMLResponse:
    """Render the compatibility view; primary navigation uses the Settings section."""
    context = dashboard_context(request, principal, "learning")
    context.update(learning_data(request))
    return render(request, "learning.html", context)


@router.post("/learning/{proposal_id}/review")
async def review_learning(request: Request, principal: Owner, proposal_id: str) -> RedirectResponse:
    """Require explicit review before applying a rule; dismissing leaves all guidance unchanged."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        if fields.get("action") == "dismiss":
            dismiss_proposal(services(request).db, proposal_id, principal.user.id)
        elif fields.get("action") == "apply" and fields.get("reviewed") == "true":
            apply_proposal(
                services(request).db,
                proposal_id,
                fields.get("rule", ""),
                int(fields.get("version", "0")),
                principal.user.id,
            )
        else:
            raise ValueError("Review the exact guidance before applying it")
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/learning", 303)


@router.post("/learning/versions/{version_id}/restore")
async def restore_guidance(request: Request, principal: Owner, version_id: str) -> RedirectResponse:
    """Restore a displayed historical version as a new current version after owner confirmation."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        if fields.get("reviewed") != "true":
            raise ValueError("Review the guidance to restore first")
        rollback(
            services(request).db, version_id, int(fields.get("version", "0")), principal.user.id
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/learning", 303)
