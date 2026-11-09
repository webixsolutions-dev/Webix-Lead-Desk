"""Explicit owner approval of a fixed diagnostic email; HTTP only queues work."""

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.core.clock import utc_now
from app.sending.diagnostics import confirm_receipt, request_test

router = APIRouter()


@router.post("/settings/mailboxes/{mailbox_id}/test")
async def queue_mailbox_test(
    request: Request, principal: Owner, mailbox_id: str
) -> RedirectResponse:
    """Bind approval to one sender, recipient and the exact fixed message shown in Settings."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("approve_test") != "true":
        raise HTTPException(422, "Approve the displayed test email and its exact recipient first")
    app = services(request)
    try:
        request_test(
            app.db,
            app.settings,
            mailbox_id,
            fields.get("recipient", ""),
            fields.get("request_id", ""),
            principal.user.id,
            utc_now(),
            signature_version=int(fields.get("signature_version", "0")),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/settings#test-mailbox-" + mailbox_id, 303)


@router.post("/settings/mailboxes/{mailbox_id}/test/{job_id}/received")
async def test_received(
    request: Request, principal: Owner, mailbox_id: str, job_id: str
) -> RedirectResponse:
    """Save the owner's confirmation only after they have checked the actual inbox."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        confirm_receipt(services(request).db, mailbox_id, job_id, principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/settings#test-mailbox-" + mailbox_id, 303)
