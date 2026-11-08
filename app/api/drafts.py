"""Authenticated draft editing and exact owner approval; no transport calls."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, Principal, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.db.delivery_models import SendIntent
from app.db.lead_models import Campaign, CampaignLead, Company, Contact
from app.db.models import AuditEvent
from app.db.outreach_models import Conversation, Draft, Mailbox
from app.db.research_models import Evidence
from app.drafting.brief import build_brief
from app.drafting.service import approval_blocks, approve, save_draft

router = APIRouter()


def outbox_context(request: Request, principal: Principal) -> dict[str, Any]:
    """Read current real drafts separately from preview examples."""
    context = dashboard_context(request, principal, "outbox")
    with services(request).db.sessions() as session:
        context["drafts"] = list(
            session.scalars(
                select(Draft)
                .where(Draft.state != "superseded")
                .order_by(Draft.created_at.desc())
                .limit(100)
            )
        )
        context["intents"] = list(
            session.scalars(select(SendIntent).order_by(SendIntent.due_at).limit(100))
        )
    return context


@router.get("/leads/{company_id}/draft", response_class=HTMLResponse)
def editor(request: Request, principal: CurrentUser, company_id: str) -> HTMLResponse:
    """Show the evidence-backed editor and a copyable brief for manual ChatGPT use."""
    context = dashboard_context(request, principal, "outbox")
    db = services(request).db
    with db.sessions() as session:
        company = session.get(Company, company_id)
        if not company:
            raise HTTPException(404, "Business not found")
        campaigns = list(
            session.scalars(
                select(Campaign)
                .join(CampaignLead)
                .where(CampaignLead.company_id == company_id, Campaign.active.is_(True))
            )
        )
        conversation = session.scalar(
            select(Conversation).where(Conversation.company_id == company_id)
        )
        try:
            step = int(request.query_params.get("step", "0"))
        except ValueError as error:
            raise HTTPException(422, "Invalid message step") from error
        if not 0 <= step <= 20:
            raise HTTPException(422, "Invalid message step")
        item = (
            session.scalar(
                select(Draft)
                .where(Draft.conversation_id == conversation.id, Draft.step == step)
                .order_by(Draft.revision.desc())
                .limit(1)
            )
            if conversation
            else None
        )
        selected = (
            conversation.campaign_id if conversation else request.query_params.get("campaign", "")
        )
        campaign = next(
            (entry for entry in campaigns if entry.id == selected),
            campaigns[0] if campaigns else None,
        )
        context.update(
            company_record=company,
            campaigns=campaigns,
            selected_campaign=campaign.id if campaign else "",
            contacts=list(session.scalars(select(Contact).where(Contact.company_id == company_id))),
            mailboxes=list(session.scalars(select(Mailbox))),
            evidence=list(
                session.scalars(
                    select(Evidence)
                    .where(Evidence.company_id == company_id)
                    .order_by(Evidence.observed_at.desc())
                    .limit(30)
                )
            ),
            draft=item,
            step=step,
            blocks=approval_blocks(db, item) if item else [],
        )
    context["brief"] = (
        build_brief(db, company_id, campaign.id) if campaign else "Create a campaign first."
    )
    return render(request, "draft_editor.html", context)


@router.post("/leads/{company_id}/draft")
async def edit_draft(request: Request, principal: CurrentUser, company_id: str) -> RedirectResponse:
    """Save an unapproved revision and redirect to its exact review page."""
    fields = await read_form(request, repeatable=frozenset({"evidence_ids"}))
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        step = int(fields.get("step", "0"))
        save_draft(
            services(request).db,
            company_id,
            fields.get("campaign_id", ""),
            fields.get("contact_id", ""),
            fields.get("mailbox_id", ""),
            fields.get("subject", ""),
            fields.get("body", ""),
            [value.strip() for value in fields.get("evidence_ids", "").split(",") if value.strip()],
            principal.user.id,
            previous_id=fields.get("previous_id") or None,
            step=step,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/leads/{company_id}/draft?step={step}", 303)


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(request: Request, principal: Owner, draft_id: str) -> RedirectResponse:
    """Bind explicit owner review to the exact rendered content fingerprint."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("reviewed") != "true":
        raise HTTPException(422, "Review the exact message and confirm its factual accuracy")
    try:
        approve(services(request).db, draft_id, fields.get("digest", ""), principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/outbox", 303)


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(request: Request, principal: Owner, draft_id: str) -> RedirectResponse:
    """Stop an unsent draft, retaining a reason for later owner-reviewed learning."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    reason = fields.get("reason", "").strip()
    if not 5 <= len(reason) <= 500:
        raise HTTPException(422, "Provide a short rejection reason")
    with services(request).db.sessions.begin() as session:
        changed = session.scalar(
            update(Draft)
            .where(Draft.id == draft_id, Draft.state.in_(["draft", "approved"]))
            .values(state="rejected")
            .returning(Draft.id)
        )
        if not changed:
            raise HTTPException(409, "This draft has already changed")
        session.add(
            AuditEvent(
                actor_id=principal.user.id,
                action="draft.rejected",
                entity_id=draft_id,
                details={"reason": reason},
            )
        )
    return RedirectResponse("/outbox", 303)


@router.post("/contacts/{contact_id}/verify")
async def verify_contact(request: Request, principal: Owner, contact_id: str) -> RedirectResponse:
    """Record manual address verification independently of legal contact-basis evidence."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    note = fields.get("note", "").strip()
    if fields.get("checked") != "true" or not 10 <= len(note) <= 500:
        raise HTTPException(422, "Confirm the exact address and record how it was verified")
    with services(request).db.sessions.begin() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(404, "Contact not found")
        contact.verification, contact.verified_at = "verified", utc_now()
        session.add(
            AuditEvent(
                actor_id=principal.user.id,
                action="contact.verified",
                entity_id=contact_id,
                details={"method": note},
            )
        )
        company_id = contact.company_id
    return RedirectResponse(f"/leads/{company_id}", 303)


@router.post("/leads/{company_id}/generate-draft")
async def queue_generation(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Queue one optional AI draft, capturing the current expected revision."""
    from app.jobs.queue import enqueue

    fields = await read_form(request, repeatable=frozenset({"evidence_ids"}))
    check_csrf(principal, fields.get("csrf_token", ""))
    app = services(request)
    if not app.settings.openai_enabled:
        raise HTTPException(
            422, "AI is disabled. Use the copyable brief or configure verified API funding."
        )
    with app.db.sessions() as session:
        contact = session.get(Contact, fields.get("contact_id", ""))
        if (
            not contact
            or contact.company_id != company_id
            or not session.get(Mailbox, fields.get("mailbox_id", ""))
        ):
            raise HTTPException(422, "Choose an existing contact and sender")
    now = utc_now()
    enqueue(
        app.db,
        f"draft:{company_id}:{now.strftime('%Y%m%d%H%M')}",
        "draft",
        {
            "company_id": company_id,
            "campaign_id": fields.get("campaign_id", ""),
            "contact_id": fields.get("contact_id", ""),
            "mailbox_id": fields.get("mailbox_id", ""),
            "actor_id": principal.user.id,
            "previous_id": fields.get("previous_id") or None,
        },
        now,
    )
    return RedirectResponse("/outbox", 303)
