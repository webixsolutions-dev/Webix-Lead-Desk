"""Owner booking board with proposed slots and explicit confirmation."""

from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.bookings.service import decide, save
from app.db.booking_models import Booking
from app.db.lead_models import Company
from app.db.pipeline_models import Opportunity

router = APIRouter()


@router.get("/bookings", response_class=HTMLResponse)
def board(request: Request, principal: Owner) -> HTMLResponse:
    """Show real proposals, plus existing pipeline meetings awaiting migration/review."""
    context = dashboard_context(request, principal, "bookings")
    with services(request).db.sessions() as session:
        rows = list(session.scalars(select(Booking).order_by(Booking.starts_at.desc()).limit(200)))
        context.update(
            bookings=[
                {
                    "item": item,
                    "local": item.starts_at.astimezone(ZoneInfo(item.timezone)).strftime(
                        "%d %b %Y · %H:%M"
                    ),
                    "edit_time": item.starts_at.astimezone(ZoneInfo(item.timezone)).strftime(
                        "%Y-%m-%dT%H:%M"
                    ),
                    "minutes": int((item.ends_at - item.starts_at).total_seconds() / 60),
                }
                for item in rows
            ],
            companies=list(session.scalars(select(Company).order_by(Company.name))),
            selected_company=request.query_params.get("company", ""),
            legacy_meetings=list(
                session.scalars(
                    select(Opportunity)
                    .where(Opportunity.meeting_at.is_not(None))
                    .order_by(Opportunity.meeting_at.desc())
                    .limit(50)
                )
            ),
        )
    return render(request, "bookings.html", context)


@router.post("/bookings")
async def propose(request: Request, principal: Owner) -> RedirectResponse:
    """Save a reviewable proposal without making a reservation in an external calendar."""
    fields = await read_form(request, max_fields=18)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        save(services(request).db, principal.user.id, fields)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/bookings", 303)


@router.post("/bookings/{booking_id}/decision")
async def decision(request: Request, principal: Owner, booking_id: str) -> RedirectResponse:
    """Require confirmation of real availability and counterpart agreement."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("action") == "confirmed" and fields.get("checked") != "true":
        raise HTTPException(422, "Check the host calendar and confirm the agreed time first")
    try:
        decide(
            services(request).db,
            principal.user.id,
            booking_id,
            int(fields.get("version", "0")),
            fields.get("action", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/bookings", 303)
