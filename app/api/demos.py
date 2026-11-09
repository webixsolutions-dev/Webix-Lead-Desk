"""Private concept editing and explicit publication of immutable read-only prospect pages."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.db.lead_models import Company
from app.db.outreach_models import Conversation
from app.db.pipeline_models import DemoVersion, Opportunity
from app.db.research_models import Evidence
from app.demos.service import KINDS, create_concept, demo_message, publish_concept, revoke_concept
from app.drafting.content import fingerprint
from app.pipeline.service import QUALIFIED

router = APIRouter()


@router.get("/demos/new/{company_id}", response_class=HTMLResponse)
def concept_editor(request: Request, principal: Owner, company_id: str) -> HTMLResponse:
    """Show owner inputs for one explicitly requested, source-grounded concept."""
    context = dashboard_context(request, principal, "demos")
    with services(request).db.sessions() as session:
        company = session.get(Company, company_id)
        card = session.scalar(
            select(Opportunity).join(Conversation).where(Conversation.company_id == company_id)
        )
        if not company or not card or card.stage not in QUALIFIED:
            raise HTTPException(422, "Qualify this conversation in Pipeline first")
        context.update(
            company_record=company,
            concept_kinds=KINDS,
            evidence=list(
                session.scalars(
                    select(Evidence)
                    .where(Evidence.company_id == company_id)
                    .order_by(Evidence.observed_at.desc())
                    .limit(30)
                )
            ),
        )
    return render(request, "concept_editor.html", context)


@router.post("/demos/new/{company_id}")
async def save_concept(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Save an unapproved concept revision; edits never alter an already shared version."""
    fields = await read_form(request, repeatable=frozenset({"evidence_ids"}))
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        item = create_concept(
            services(request).db,
            company_id,
            fields.get("kind", ""),
            fields.get("headline", ""),
            fields.get("introduction", ""),
            [line.strip() for line in fields.get("steps", "").splitlines() if line.strip()],
            [value for value in fields.get("evidence_ids", "").split(",") if value],
            principal.user.id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/demos/{item.id}", 303)


@router.get("/demos/{demo_id}", response_class=HTMLResponse)
def review_concept(request: Request, principal: CurrentUser, demo_id: str) -> HTMLResponse:
    """Show the private exact concept and separate publication/message approval actions."""
    context = dashboard_context(request, principal, "demos")
    with services(request).db.sessions() as session:
        demo = session.get(DemoVersion, demo_id)
        if not demo:
            raise HTTPException(404, "Concept not found")
        context.update(
            demo=demo,
            concept_kinds=KINDS,
            public_origin_ready=bool(services(request).settings.public_base_url),
        )
    return render(request, "concept_review.html", context)


@router.post("/demos/{demo_id}/publish")
async def approve_concept(request: Request, principal: Owner, demo_id: str) -> RedirectResponse:
    """Publish only after an owner confirms the exact content and intended prospect sharing."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("reviewed") != "true":
        raise HTTPException(422, "Review this exact concept before creating a public share link")
    try:
        publish_concept(
            services(request).db,
            services(request).settings,
            demo_id,
            fields.get("digest", ""),
            principal.user.id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/demos/{demo_id}", 303)


@router.post("/demos/{demo_id}/message")
async def prepare_concept_message(
    request: Request, principal: Owner, demo_id: str
) -> RedirectResponse:
    """Create or reopen the unapproved message, locked to the original sender and thread."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        draft = demo_message(
            services(request).db, demo_id, fields.get("body", ""), principal.user.id
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(
        f"/leads/{draft.snapshot['company_id']}/draft?step={draft.step}&kind=concept", 303
    )


@router.post("/demos/{demo_id}/revoke")
async def stop_concept_sharing(
    request: Request, principal: Owner, demo_id: str
) -> RedirectResponse:
    """Revoke an explicitly selected share link; the underlying audit record remains."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        revoke_concept(services(request).db, demo_id, principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/demos/{demo_id}", 303)


@router.get("/concepts/{share_token}", response_class=HTMLResponse)
def shared_concept(request: Request, share_token: str) -> HTMLResponse:
    """Serve only an explicitly approved immutable concept, without exposing internal evidence."""
    if not 32 <= len(share_token) <= 64:
        raise HTTPException(404, "Concept unavailable")
    with services(request).db.sessions() as session:
        demo = session.scalar(
            select(DemoVersion).where(
                DemoVersion.share_token == share_token, DemoVersion.state == "approved"
            )
        )
        if not demo or fingerprint(demo.snapshot) != demo.digest:
            raise HTTPException(404, "Concept unavailable")
    return render(request, "concept_public.html", {"demo": demo, "concept_kinds": KINDS})
