"""Owner signature editing and isolated, authenticated email previews."""

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.core.preferences import SettingsConflict
from app.db.outreach_models import Draft, Mailbox
from app.sending.diagnostics import BODY, SUBJECT
from app.sending.signature_html import html_message
from app.sending.signatures import get_signature, save_signature

router = APIRouter()
PREVIEW_CSP = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src https:; "
    "connect-src 'none'; frame-src 'none'; frame-ancestors 'self'; form-action 'none'; "
    "base-uri 'none'; object-src 'none'; sandbox"
)


async def _save_signature(
    request: Request, principal: Owner, target_id: str, *, campaign_override: bool = False
) -> None:
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        save_signature(
            services(request).db,
            target_id,
            fields.get("html", ""),
            int(fields.get("version", "-1")),
            principal.user.id,
            campaign_override=campaign_override,
        )
    except SettingsConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/settings/mailboxes/{mailbox_id}/signature")
async def edit_signature(request: Request, principal: Owner, mailbox_id: str) -> RedirectResponse:
    """Save the mailbox default without approving or sending a message."""
    await _save_signature(request, principal, mailbox_id)
    return RedirectResponse("/settings#signature-" + mailbox_id, 303)


@router.post("/campaigns/{campaign_id}/signature")
async def edit_campaign_signature(
    request: Request, principal: Owner, campaign_id: str
) -> RedirectResponse:
    """Save the owner-reviewed signature for an assigned campaign sender."""
    await _save_signature(request, principal, campaign_id, campaign_override=True)
    return RedirectResponse("/leads/campaigns#campaign-signature-" + campaign_id, 303)


@router.get("/settings/mailboxes/{mailbox_id}/signature-preview")
def signature_preview(
    request: Request, principal: Owner, mailbox_id: str, version: int, test: bool = False
) -> HTMLResponse:
    """Preview an exact saved revision in a script-free sandbox with no referrer."""
    with services(request).db.sessions() as session:
        box = session.get(Mailbox, mailbox_id)
        if not box:
            raise HTTPException(404, "Mailbox not found")
        signature = get_signature(session, mailbox_id)
        if signature["version"] != version:
            raise HTTPException(409, "Signature changed. Refresh Settings to review it.")
        snapshot = {
            "subject": SUBJECT if test else "Email signature",
            "body": BODY if test else "",
            "signature": signature,
        }
    request.state.email_preview = True
    return HTMLResponse(html_message(snapshot))


@router.get("/drafts/{draft_id}/email-preview")
def draft_preview(request: Request, principal: CurrentUser, draft_id: str) -> HTMLResponse:
    """Render only the frozen draft, not the sender's potentially newer signature."""
    with services(request).db.sessions() as session:
        draft = session.get(Draft, draft_id)
        if not draft:
            raise HTTPException(404, "Draft not found")
        try:
            html = html_message(draft.snapshot)
        except ValueError as error:
            raise HTTPException(409, "Save and review a new signature revision") from error
    request.state.email_preview = True
    return HTMLResponse(html)
