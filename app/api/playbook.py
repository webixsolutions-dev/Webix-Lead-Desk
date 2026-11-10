"""Owner-managed guidance, sender identities, editable offers and verified proof cards."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.responses import RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.core.clock import utc_now
from app.db.lead_models import Campaign
from app.db.models import AuditEvent
from app.db.outreach_models import LocalTeamMember, Mailbox, Offer, PlaybookVersion, ProofCard
from app.db.research_models import AICall, BudgetAccount
from app.playbook.catalog import BILLING_PERIODS, CURRENCIES, labels, offer_values, service_names
from app.playbook.service import publish
from app.sending.providers import PERSONAL_DOMAINS, PRESETS, connection
from app.sources.validation import email_address, website_url

router = APIRouter()


def settings_data(request: Request) -> dict[str, Any]:
    """Read non-secret configuration and version history for authenticated settings."""
    from app.api.delivery import connection_data
    from app.audit.monthly import CAPS, TOTAL, usage
    from app.playbook.industries import catalog
    from app.sending.delegation import read_policy

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
        monthly_usage = usage(session, utc_now())
        return {
            "industry_playbooks": catalog(),
            "seed_guidance": next(
                (item["guidance"] for item in catalog() if scope == "industry:" + item["slug"]), ""
            ),
            "approval_policy": read_policy(session)[0],
            "approval_policy_version": read_policy(session)[1],
            "ai_monthly_usage": monthly_usage,
            "ai_monthly_caps": CAPS,
            "ai_monthly_total": TOTAL,
            "ai_monthly_used": sum(monthly_usage.values()),
            **connection_data(request),
            "playbook_scope": scope,
            "playbook_versions": versions,
            "current_playbook": versions[0] if versions else None,
            "campaigns": list(session.scalars(select(Campaign).limit(100))),
            "mailboxes": list(session.scalars(select(Mailbox))),
            "local_team_members": list(session.scalars(select(LocalTeamMember))),
            "offers": list(session.scalars(select(Offer))),
            "service_names": service_names(session),
            "currencies": CURRENCIES,
            "billing_periods": BILLING_PERIODS,
            "proofs": list(session.scalars(select(ProofCard))),
            "ai_budget": session.get(BudgetAccount, "primary"),
            "ai_calls": list(
                session.scalars(select(AICall).order_by(AICall.created_at.desc()).limit(10))
            ),
            "ai_enabled": services(request).settings.openai_enabled,
            "ai_audit_model": services(request).settings.openai_audit_model
            or services(request).settings.openai_model,
            "ai_draft_model": services(request).settings.openai_draft_model
            or services(request).settings.openai_model,
            "ai_processing": services(request).settings.openai_processing,
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
    """Save a disabled Titan or Gmail sender identity with a complete, owner-provided footer."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        email = email_address(fields.get("email", ""))
        lane = fields.get("lane", "client")
        provider = fields.get("provider", "titan")
        profile = {
            key: fields.get(key, "").strip()
            for key in ("brand", "sender_name", "mailing_address", "contact_details")
        }
        if (
            not email
            or provider not in PRESETS
            or (provider == "titan" and email.split("@")[-1] in {"gmail.com", "googlemail.com"})
            or (lane == "partner" and email.split("@")[-1] in PERSONAL_DOMAINS)
            or lane not in {"client", "partner"}
            or any(
                not 2 <= len(value) <= (120 if key in {"brand", "sender_name"} else 500)
                or any(c in value for c in "\r\n\x00")
                for key, value in profile.items()
            )
        ):
            raise ValueError(
                "Provide the actual sender identity and complete mailing/contact details"
            )
        with services(request).db.sessions.begin() as session:
            session.add(
                Mailbox(
                    email=email,
                    provider=provider,
                    lane=lane,
                    connection=connection(provider),
                    **profile,
                )
            )
    except (ValueError, IntegrityError) as error:
        raise HTTPException(
            422, "Check sender details; this address may already have a profile"
        ) from error
    return RedirectResponse("/settings", 303)


@router.post("/settings/offers")
async def save_offer(request: Request, principal: Owner) -> RedirectResponse:
    """Create or update reusable quotes without altering outgoing drafts."""
    fields = await read_form(request, max_fields=16)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        values = offer_values(fields)
        version = int(fields.get("version", "0"))
        with services(request).db.sessions.begin() as session:
            if fields.get("offer_id"):
                existing = session.get(Offer, fields["offer_id"])
                if existing and existing.service != values["service"]:
                    raise ValueError("Keep the service family; add a new offer for another service")
                changed = session.scalar(
                    update(Offer)
                    .where(Offer.id == fields["offer_id"], Offer.version == version)
                    .values(**values, version=version + 1)
                    .returning(Offer.id)
                )
                if not changed:
                    raise ValueError("Offer changed. Reload before saving.")
                offer_id = changed
            else:
                offer = Offer(**values)
                session.add(offer)
                session.flush()
                offer_id = offer.id
            session.add(
                AuditEvent(actor_id=principal.user.id, action="offer.saved", entity_id=offer_id)
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/settings?scope=global#offers", 303)


@router.post("/settings/proofs")
async def add_proof(request: Request, principal: Owner) -> RedirectResponse:
    """Record or revise a scoped fact only after explicit owner verification."""
    fields = await read_form(request, max_fields=16)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        source = website_url(fields.get("source_url", ""))
        title, fact = fields.get("title", "").strip(), fields.get("fact", "").strip()
        attribution = fields.get("attribution", "").strip()
        proof_services = labels(fields.get("services", ""))
        industries = labels(fields.get("industries", ""))
        if (
            not source
            or not 3 <= len(title) <= 160
            or not 10 <= len(fact) <= 2000
            or fields.get("verified") != "true"
            or len(attribution) > 120
        ):
            raise ValueError("Confirm the case fact and its source")
        with services(request).db.sessions.begin() as session:
            values = dict(
                title=title,
                fact=fact,
                source_url=source,
                approved_by=principal.user.id,
                approved_at=utc_now(),
                services=proof_services,
                industries=industries,
                attribution=attribution,
            )
            if fields.get("proof_id"):
                version = int(fields.get("version", "0"))
                proof_id = session.scalar(
                    update(ProofCard)
                    .where(ProofCard.id == fields["proof_id"], ProofCard.version == version)
                    .values(**values, version=version + 1)
                    .returning(ProofCard.id)
                )
                if not proof_id:
                    raise ValueError("Proof changed. Reload before saving.")
            else:
                proof = ProofCard(**values)
                session.add(proof)
                session.flush()
                proof_id = proof.id
            session.add(
                AuditEvent(actor_id=principal.user.id, action="proof.saved", entity_id=proof_id)
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/settings?scope=global#proofs", 303)


@router.post("/settings/local-team")
async def save_local_member(request: Request, principal: Owner) -> RedirectResponse:
    """Record or disable a real local team member with explicit owner confirmation."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    name, city, country = (fields.get(k, "").strip() for k in ("name", "city", "country"))
    if (
        not 2 <= len(name) <= 120
        or len(city) > 120
        or country not in {"CA", "US"}
        or fields.get("confirmed") != "true"
    ):
        raise HTTPException(422, "Confirm a real person's name and city/country")
    with services(request).db.sessions.begin() as session:
        member = session.get(LocalTeamMember, fields.get("member_id", ""))
        if member is None:
            member = LocalTeamMember(
                name=name, city=city, country=country, reviewed_by=principal.user.id
            )
            session.add(member)
        member.name, member.city, member.country = name, city, country
        member.active = fields.get("active") == "true"
        member.reviewed_by = principal.user.id
        member.reviewed_at = utc_now()
    return RedirectResponse("/settings#local-team", 303)


@router.post("/settings/offers/bundle-templates")
async def bundle_templates(request: Request, principal: Owner) -> RedirectResponse:
    """Create internal package suggestions without activating any service or sending."""
    from app.playbook.bundles import install_templates

    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    with services(request).db.sessions.begin() as session:
        install_templates(session, principal.user.id)
    return RedirectResponse("/settings?scope=global#offers", 303)
