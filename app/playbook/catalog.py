"""Reusable service labels, exact quote amounts and relevant proof selection."""

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.lead_models import Campaign
from app.db.outreach_models import Offer, ProofCard

CURRENCIES = ("CAD", "USD", "AED", "NZD", "PKR", "GBP", "EUR", "AUD")
BILLING_PERIODS = ("monthly", "quarterly", "yearly")


def labels(value: str) -> list[str]:
    """Parse bounded comma-separated labels, preserving spelling and removing duplicates."""
    result: dict[str, str] = {}
    for item in value.split(","):
        label = item.strip()
        if not label:
            continue
        if len(label) > 120 or any(ord(c) < 32 for c in label):
            raise ValueError("Use service or industry names up to 120 characters")
        result.setdefault(label.casefold(), label)
    if len(result) > 30:
        raise ValueError("Use no more than 30 service or industry labels")
    return list(result.values())


def _cents(value: str) -> int:
    try:
        amount = Decimal(value)
        if not amount.is_finite() or not 0 <= amount <= 1_000_000:
            raise ValueError("Use a price from 0 to 1,000,000")
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise ValueError("Use at most two decimal places")
        return int(cents)
    except (InvalidOperation, OverflowError) as error:
        raise ValueError("Enter both fixed prices, or choose Custom quote") from error


def offer_values(fields: dict[str, str]) -> dict[str, Any]:
    """Validate explicit prices; an unknown quote stores NULL instead of a free price."""
    service = fields.get("service", "").strip()
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    # Older forms supply both CAD amounts. New forms always send pricing_mode.
    mode = fields.get("pricing_mode", "fixed" if fields.get("setup_cad") else "quote")
    currency = fields.get("currency", "CAD")
    period = fields.get("billing_period", "monthly")
    if (
        not 2 <= len(service) <= 120
        or "," in service
        or len(name) > 120
        or not 10 <= len(description) <= 2000
        or mode not in {"fixed", "quote"}
        or currency not in CURRENCIES
        or period not in BILLING_PERIODS
    ):
        raise ValueError("Check the service, scope, price mode, currency and billing period")
    return {
        "service": service,
        "name": name,
        "description": description,
        "pricing_mode": mode,
        "currency": currency,
        "billing_period": period,
        "setup_cad_cents": _cents(fields.get("setup_cad", "")) if mode == "fixed" else None,
        "recurring_cad_cents": _cents(fields.get("recurring_cad", "")) if mode == "fixed" else None,
        "active": fields.get("active") == "true",
    }


def service_names(session: Session) -> list[str]:
    """List saved service families once, including paused families for owner editing."""
    return sorted(set(session.scalars(select(Offer.service))), key=str.casefold)


def matching_proofs(session: Session, campaign: Campaign) -> list[ProofCard]:
    """Exclude unrelated or unassigned proof before it can reach a drafting model."""
    wanted = {item.casefold() for item in campaign.services}
    industry = campaign.industry.strip().casefold()
    matches = []
    for proof in session.scalars(
        select(ProofCard).order_by(ProofCard.approved_at.desc(), ProofCard.id)
    ):
        if not wanted.intersection(item.casefold() for item in proof.services):
            continue
        if proof.industries and industry not in {item.casefold() for item in proof.industries}:
            continue
        if not proof.attribution.strip():
            continue
        matches.append(proof)
        if len(matches) == 5:
            break
    return matches
