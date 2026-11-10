"""Owner-only meeting preparation, proposal review and private Word exports."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.bookings.packs import prepare
from app.core.clock import utc_now
from app.db.booking_models import Booking
from app.db.lead_models import Company
from app.db.outreach_models import Mailbox
from app.db.proposal_models import MeetingPack, Proposal, ProposalFollowup
from app.proposals import actions, notes, service, word
from app.proposals.followups import hold_reason

router = APIRouter()


@router.get("/pipeline/proposals", response_class=HTMLResponse)
def board(request: Request, principal: Owner) -> HTMLResponse:
    """List real revisions and due review reminders, including proposals without email threads."""
    context = dashboard_context(request, principal, "pipeline")
    with services(request).db.sessions() as session:
        context["proposals"] = list(
            session.scalars(select(Proposal).order_by(Proposal.created_at.desc()).limit(200))
        )
        context["followups"] = list(
            session.execute(
                select(ProposalFollowup, Proposal)
                .join(Proposal)
                .where(ProposalFollowup.state == "pending")
                .order_by(ProposalFollowup.due_at)
                .limit(200)
            )
        )
    context["now"] = utc_now()
    return render(request, "proposals.html", context)


@router.get("/bookings/{booking_id}/pack", response_class=HTMLResponse)
def meeting_pack(request: Request, principal: Owner, booking_id: str) -> HTMLResponse:
    """Read the bounded preparation snapshot and confirmed meeting notes."""
    context = dashboard_context(request, principal, "pipeline")
    with services(request).db.sessions() as session:
        booking = session.get(Booking, booking_id)
        if not booking:
            raise HTTPException(404, "Booking not found")
        context.update(
            booking=booking,
            company=session.get(Company, booking.company_id),
            pack=session.scalar(
                select(MeetingPack)
                .where(MeetingPack.booking_id == booking.id)
                .order_by(MeetingPack.booking_version.desc())
                .limit(1)
            ),
            note=notes.latest_note(session, booking_id),
        )
    return render(request, "meeting_pack.html", context)


@router.post("/bookings/{booking_id}/pack")
async def refresh_pack(request: Request, principal: Owner, booking_id: str) -> RedirectResponse:
    """Prepare a missing legacy snapshot without paid research or modifying prior snapshots."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        with services(request).db.sessions.begin() as session:
            booking = notes.lock_booking(session, booking_id, principal.user.id)
            if not session.scalar(
                select(MeetingPack.id).where(
                    MeetingPack.booking_id == booking_id,
                    MeetingPack.booking_version == booking.version,
                )
            ):
                prepare(session, booking)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/bookings/{booking_id}/pack", 303)


@router.post("/bookings/{booking_id}/notes")
async def confirm_notes(request: Request, principal: Owner, booking_id: str) -> RedirectResponse:
    """Require explicit confirmation of actual meeting facts, not incoming text instructions."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("checked") != "true":
        raise HTTPException(422, "Confirm these are accurate notes of the completed meeting")
    try:
        notes.confirm_notes(
            services(request).db,
            principal.user.id,
            booking_id,
            int(fields.get("booking_version", "0")),
            int(fields.get("version", "0")),
            fields.get("notes", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/bookings/{booking_id}/proposal", 303)


@router.get("/bookings/{booking_id}/proposal", response_class=HTMLResponse)
def editor(request: Request, principal: Owner, booking_id: str) -> HTMLResponse:
    """Use confirmed notes as an editable starting point; leave all amounts for the owner."""
    context = dashboard_context(request, principal, "pipeline")
    with services(request).db.sessions() as session:
        booking = session.get(Booking, booking_id)
        if not booking:
            raise HTTPException(404, "Booking not found")
        note = notes.latest_note(session, booking_id)
        proposal = session.scalar(
            select(Proposal)
            .where(Proposal.booking_id == booking_id)
            .order_by(Proposal.version.desc())
            .limit(1)
        )
        context.update(
            booking=booking,
            note=note,
            proposal=proposal,
            mailboxes=list(session.scalars(select(Mailbox))),
            sections=service.SECTIONS,
            currencies=service.CURRENCIES,
            data=proposal.snapshot
            if proposal
            else {"sections": {"summary": note.text if note else ""}, "sender": {}},
        )
    return render(request, "proposal_editor.html", context)


@router.post("/bookings/{booking_id}/proposal")
async def save(request: Request, principal: Owner, booking_id: str) -> RedirectResponse:
    """Save only an unapproved immutable revision; no model derives prices."""
    fields = await read_form(request, max_fields=20, maximum=65000)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        proposal = service.create(services(request).db, principal.user.id, booking_id, fields)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/proposals/" + proposal.id, 303)


@router.get("/proposals/{proposal_id}", response_class=HTMLResponse)
def review(request: Request, principal: Owner, proposal_id: str) -> HTMLResponse:
    """Show the exact identity, recipient, scope, prices and independent delivery state."""
    context = dashboard_context(request, principal, "pipeline")
    with services(request).db.sessions() as session:
        item = session.get(Proposal, proposal_id)
        if not item:
            raise HTTPException(404, "Proposal not found")
        context.update(
            proposal=item,
            data=item.snapshot,
            sections=service.SECTIONS,
            money=word.money,
            followup_hold=hold_reason(session, item) if item.sent_at else "",
            followups=list(
                session.scalars(
                    select(ProposalFollowup)
                    .where(ProposalFollowup.proposal_id == item.id)
                    .order_by(ProposalFollowup.day)
                )
            ),
        )
        try:
            booking = session.get(Booking, item.booking_id)
            if booking:
                service.require_current(session, item, booking)
            context["stale"] = ""
        except ValueError as error:
            context["stale"] = str(error)
    return render(request, "proposal_review.html", context)


@router.post("/proposals/{proposal_id}/approve")
async def approve(request: Request, principal: Owner, proposal_id: str) -> RedirectResponse:
    """Bind owner approval to the exact visible revision, independent of any email approval."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("checked") != "true":
        raise HTTPException(422, "Review this exact recipient, sender, scope and price first")
    try:
        service.approve(
            services(request).db, principal.user.id, proposal_id, fields.get("digest", "")
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/proposals/" + proposal_id, 303)


@router.get("/proposals/{proposal_id}/word")
def export_word(request: Request, principal: Owner, proposal_id: str) -> Response:
    """Download the current private document; stale and superseded revisions cannot be exported."""
    with services(request).db.sessions() as session:
        item = session.get(Proposal, proposal_id)
        booking = session.get(Booking, item.booking_id) if item else None
        if not item or not booking:
            raise HTTPException(404, "Proposal not found")
        try:
            service.require_current(session, item, booking)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        content = word.export(item.snapshot, approved=item.state in {"approved", "sent"})
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="proposal-v{item.version}.docx"'},
    )


@router.post("/proposals/{proposal_id}/sent")
async def sent(request: Request, principal: Owner, proposal_id: str) -> RedirectResponse:
    """Record an already performed manual delivery of this exact approved document."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("checked") != "true":
        raise HTTPException(422, "Confirm actual delivery of the exact approved document")
    try:
        actions.record_sent(
            services(request).db,
            principal.user.id,
            proposal_id,
            fields.get("digest", ""),
            datetime.fromisoformat(fields.get("sent_at", "")),
            fields.get("reference", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/proposals/" + proposal_id, 303)


@router.post("/proposal-followups/{followup_id}")
async def followup(request: Request, principal: Owner, followup_id: str) -> RedirectResponse:
    """Record the human review outcome; any message still needs its own exact approval."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        actions.review_followup(
            services(request).db,
            principal.user.id,
            followup_id,
            fields.get("state", ""),
            fields.get("note", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/pipeline/proposals", 303)
