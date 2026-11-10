"""Owner controls for exact batch approval and optional scoped outreach delegation."""

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.api.drafts import outbox_context
from app.api.views import render
from app.drafting.service import approve
from app.sending.delegation import save_policy
from app.sending.schedule import schedule

router = APIRouter()


@router.post("/settings/outreach-approval")
async def policy(request: Request, principal: Owner) -> RedirectResponse:
    """Enable only owner-selected campaigns; all replies remain manual."""
    fields = await read_form(request, repeatable=frozenset({"campaigns"}), max_fields=110)
    check_csrf(principal, fields.get("csrf_token", ""))
    mode = fields.get("mode", "manual")
    if mode != "manual" and fields.get("delegated") != "true":
        raise HTTPException(422, "Confirm delegation for the selected campaigns")
    try:
        save_policy(
            services(request).db,
            principal.user.id,
            int(fields.get("version", "0")),
            mode,
            [v for v in fields.get("campaigns", "").split(",") if v],
            int(fields.get("daily_limit", "10")),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/settings#approval", 303)


@router.post("/drafts/approve-batch")
async def batch(request: Request, principal: Owner) -> HTMLResponse:
    """Approve only individually selected, displayed exact revisions, up to 100."""
    fields = await read_form(request, repeatable=frozenset({"selected"}), max_fields=104)
    check_csrf(principal, fields.get("csrf_token", ""))
    selected = list(dict.fromkeys(fields.get("selected", "").split(",")))
    if fields.get("reviewed") != "true" or not selected or len(selected) > 100 or not all(selected):
        raise HTTPException(422, "Select the exact messages you reviewed (1–100)")
    outcomes = []
    for value in selected:
        parts = value.split(":")
        if len(parts) != 2 or len(parts[0]) != 36 or len(parts[1]) != 64:
            raise HTTPException(422, "Invalid message selection; reload the outbox")
    for value in selected:
        draft_id, digest = value.split(":")
        try:
            approve(services(request).db, draft_id, digest, principal.user.id)
            if fields.get("schedule") == "true":
                schedule(services(request).db, draft_id, principal.user.id)
            outcomes.append(
                "Approved"
                + (" and scheduled" if fields.get("schedule") == "true" else "")
                + ": "
                + draft_id
            )
        except ValueError as error:
            outcomes.append("Needs review: " + draft_id + " — " + str(error))
    context = outbox_context(request, principal)
    context["batch_outcomes"] = outcomes
    return render(request, "outbox.html", context)
