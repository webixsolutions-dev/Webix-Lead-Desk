"""Private owner settings for native WhatsApp agents; HTTP handlers never call the provider."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import Owner, Principal, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.db.models import WorkerInstance
from app.db.whatsapp_models import WhatsAppConnection, WhatsAppNotice
from app.whatsapp.settings import configure, disconnect

router = APIRouter()


def page(request: Request, principal: Principal, code: str = "") -> HTMLResponse:
    """Render safe status and a one-time pairing code, never the saved API key."""
    context = dashboard_context(request, principal, "settings")
    with services(request).db.sessions() as session:
        worker = session.get(WorkerInstance, "primary")
        context["whatsapp_worker"] = bool(
            worker and (worker.runtime_config or {}).get("whatsapp_agent") == 1
        )
        context["connection"] = session.get(WhatsAppConnection, "primary")
        context["notices"] = list(
            session.scalars(
                select(WhatsAppNotice).order_by(WhatsAppNotice.created_at.desc()).limit(20)
            )
        )
    context["pair_code"] = code
    return render(request, "whatsapp.html", context)


@router.get("/settings/whatsapp", response_class=HTMLResponse)
def settings_page(request: Request, principal: Owner) -> HTMLResponse:
    """Show connection instructions and redacted delivery status to the owner only."""
    return page(request, principal)


@router.post("/settings/whatsapp", response_class=HTMLResponse)
async def save(request: Request, principal: Owner) -> HTMLResponse:
    """Encrypt the key, explicitly choose detail sharing, and start a fresh pairing."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("confirmed") != "true":
        raise HTTPException(422, "Confirm this is your own private WhatsApp agent")
    try:
        code = configure(
            services(request).db,
            services(request).settings,
            principal.user.id,
            fields.get("api_key", ""),
            fields.get("share_details") == "true",
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return page(request, principal, code)


@router.post("/settings/whatsapp/disconnect")
async def remove(request: Request, principal: Owner) -> RedirectResponse:
    """Revoke local access and every outstanding WhatsApp review code."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    disconnect(services(request).db, principal.user.id)
    return RedirectResponse("/settings/whatsapp", 303)
