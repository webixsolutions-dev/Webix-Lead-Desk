"""Owner-managed guidance, sender identities, editable offers and verified proof cards."""

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.responses import RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.db.lead_models import Campaign
from app.db.outreach_models import Mailbox, Offer, PlaybookVersion, ProofCard
from app.db.research_models import AICall, BudgetAccount
from app.playbook.service import publish
from app.sources.validation import email_address, website_url

router = APIRouter()


def settings_data(request: Request) -> dict[str, Any]:
    """Read non-secret configuration and version history for authenticated settings."""
    from app.api.delivery import connection_data

    scope = request.query_params.get("scope", "global")
    with services(request).db.sessions() as session:
        versions = list(
            session.scalars(
                select(PlaybookVersion)
                .where(PlaybookVersion.scope == scope)
                .order_by(PlaybookVersion.version.desc())
                .limit(20)
            )
        )
        return {
            **connection_data(request),
            "playbook_scope": scope,
            "playbook_versions": versions,
            "current_playbook": versions[0] if versions else None,
            "campaigns": list(session.scalars(select(Campaign).limit(100))),
            "mailboxes": list(session.scalars(select(Mailbox))),
            "offers": list(session.scalars(select(Offer))),
            "proofs": list(session.scalars(select(ProofCard))),
            "ai_budget": session.get(BudgetAccount, "primary"),
            "ai_calls": list(
                session.scalars(select(AICall).order_by(AICall.created_at.desc()).limit(10))
            ),
            "ai_enabled": services(request).settings.openai_enabled,
        }


@router.post("/settings/playbook")
async def publish_playbook(request: Request, principal: Owner) -> RedirectResponse:
    """Publish explicit owner guidance with an optimistic expected-version check."""
    fields = await read_form(request, maximum=50_000)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        scope = fields.get("scope", "global")
        publish(
            services(request).db,
            scope,
            fields.get("guidance", ""),
            int(fields.get("version", "0")),
            principal.user.id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/settings?scope={scope}", 303)


@router.post("/settings/mailboxes")
async def create_mailbox(request: Request, principal: Owner) -> RedirectResponse:
    """Save a disabled Titan sender identity with a complete, owner-provided footer."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        email = email_address(fields.get("email", ""))
        lane = fields.get("lane", "client")
        profile = {
            key: fields.get(key, "").strip()
            for key in ("brand", "sender_name", "mailing_address", "contact_details")
        }
        if (
            not email
            or email.split("@")[-1] in {"gmail.com", "googlemail.com"}
            or lane not in {"client", "partner"}
            or any(
                not 2 <= len(value) <= (120 if key in {"brand", "sender_name"} else 500)
                or any(c in value for c in "\r\n\x00")
                for key, value in profile.items()
            )
        ):
            raise ValueError(
                "Provide the actual Titan sender identity and complete mailing/contact details"
            )
        with services(request).db.sessions.begin() as session:
            session.add(Mailbox(email=email, provider="titan", lane=lane, **profile))
    except (ValueError, IntegrityError) as error:
        raise HTTPException(
            422, "Check sender details; this address may already have a profile"
        ) from error
    return RedirectResponse("/settings", 303)


@router.post("/settings/offers")
async def save_offer(request: Request, principal: Owner) -> RedirectResponse:
    """Create or update owner-defined CAD pricing without altering outgoing drafts."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        service, description = (
            fields.get("service", "").strip(),
            fields.get("description", "").strip(),
        )
        setup = int(Decimal(fields.get("setup_cad", "")) * 100)
        recurring = int(Decimal(fields.get("recurring_cad", "")) * 100)
        version = int(fields.get("version", "0"))
        if (
            not 2 <= len(service) <= 120
            or not 10 <= len(description) <= 2000
            or not 0 <= setup <= 100_000_000
            or not 0 <= recurring <= 100_000_000
        ):
            raise ValueError("Check offer details and prices")
        values = {
            "service": service,
            "description": description,
            "setup_cad_cents": setup,
            "recurring_cad_cents": recurring,
            "active": fields.get("active") == "true",
        }
        with services(request).db.sessions.begin() as session:
            if fields.get("offer_id"):
                changed = session.scalar(
                    update(Offer)
                    .where(Offer.id == fields["offer_id"], Offer.version == version)
                    .values(**values, version=version + 1)
                    .returning(Offer.id)
                )
                if not changed:
                    raise ValueError("Offer changed. Reload before saving.")
            else:
                session.add(Offer(**values))
    except (ValueError, InvalidOperation, OverflowError) as error:
        raise HTTPException(422, "Check the offer fields and current version") from error
    return RedirectResponse("/settings", 303)


@router.post("/settings/proofs")
async def add_proof(request: Request, principal: Owner) -> RedirectResponse:
    """Record a genuine case fact only after explicit owner verification."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        source = website_url(fields.get("source_url", ""))
        title, fact = fields.get("title", "").strip(), fields.get("fact", "").strip()
        if (
            not source
            or not 3 <= len(title) <= 160
            or not 10 <= len(fact) <= 2000
            or fields.get("verified") != "true"
        ):
            raise ValueError("Confirm the case fact and its source")
        with services(request).db.sessions.begin() as session:
            session.add(
                ProofCard(title=title, fact=fact, source_url=source, approved_by=principal.user.id)
            )
    except ValueError as error:
        raise HTTPException(422, "Provide a verified fact, title and source URL") from error
    return RedirectResponse("/settings", 303)
