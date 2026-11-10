"""Explainable, versioned lead scores independent of AI availability."""

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.preferences import SettingsConflict
from app.db.acquisition_models import LeadScore, ScorePolicy
from app.db.lead_models import Campaign, CampaignLead, Company
from app.db.research_models import Evidence
from app.db.session import Database
from app.journey.events import emit

LABELS = {
    "industry": "Target industry",
    "active_ads": "Active ads confirmed",
    "weak_mobile": "Weak or broken mobile website",
    "no_website": "No website confirmed",
    "response_gap": "Quote response or after-hours booking gap confirmed",
    "reviews": "At least ten Google reviews confirmed",
    "mature": "Mature tooling or strong modern website",
}


class Rules(BaseModel):
    """Owner-adjustable points and thresholds, with deterministic defaults."""

    model_config = ConfigDict(extra="forbid")
    industry: int = Field(default=2, ge=-10, le=10)
    active_ads: int = Field(default=2, ge=-10, le=10)
    weak_mobile: int = Field(default=2, ge=-10, le=10)
    no_website: int = Field(default=3, ge=-10, le=10)
    response_gap: int = Field(default=2, ge=-10, le=10)
    reviews: int = Field(default=1, ge=-10, le=10)
    mature: int = Field(default=-3, ge=-10, le=10)
    contact_now: int = Field(default=7, ge=1, le=10)
    later: int = Field(default=4, ge=0, le=9)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> "Rules":
        """Require nonoverlapping bands."""
        if self.later >= self.contact_now:
            raise ValueError("Later threshold must be below Contact now")
        return self


def current_rules(db: Database) -> tuple[Rules, int]:
    """Return the latest immutable rule revision, or documented defaults."""
    with db.sessions() as session:
        policy = session.scalar(select(ScorePolicy).order_by(ScorePolicy.version.desc()).limit(1))
        return (Rules.model_validate(policy.rules), policy.version) if policy else (Rules(), 0)


def save_rules(db: Database, rules: Rules, version: int, actor: str) -> None:
    """Append a revision only if the editor used the current version."""
    try:
        with db.sessions.begin() as session:
            current = (
                session.scalar(
                    select(ScorePolicy.version).order_by(ScorePolicy.version.desc()).limit(1)
                )
                or 0
            )
            if current != version:
                raise SettingsConflict("Scoring rules changed; reload before saving")
            session.add(ScorePolicy(version=version + 1, rules=rules.model_dump(), actor_id=actor))
    except IntegrityError as error:
        raise SettingsConflict("Scoring rules changed; reload before saving") from error


def score(db: Database, company_id: str, campaign_id: str) -> LeadScore:
    """Use only attributable observations, preserve unknowns and record every change."""
    rules, version = current_rules(db)
    with db.sessions.begin() as session:
        company, campaign = session.get(Company, company_id), session.get(Campaign, campaign_id)
        if (
            not company
            or not campaign
            or not session.scalar(
                select(CampaignLead.id).where(
                    CampaignLead.company_id == company_id, CampaignLead.campaign_id == campaign_id
                )
            )
        ):
            raise ValueError("Choose a campaign containing this business")
        facts = list(
            session.scalars(
                select(Evidence)
                .where(Evidence.company_id == company_id, Evidence.reviewed_by.is_not(None))
                .order_by(Evidence.observed_at.desc(), Evidence.id.desc())
            )
        )
        breakdown: list[dict[str, Any]] = []
        targets = [
            item.strip().casefold()
            for item in re.split(r"[,;&]", campaign.industry)
            if item.strip()
        ]
        industry = any(
            re.search(r"\b" + re.escape(item) + r"\b", company.industry.casefold())
            for item in targets
        )
        for signal, label in LABELS.items():
            evidence = next(
                (item for item in facts if signal in item.facts.get("signals", {})), None
            )
            value = evidence.facts["signals"][signal] if evidence else None
            active = industry if signal == "industry" else (type(value) is bool and value)
            if signal == "mature" and any(
                not re.search(r"website|web design|redesign", service, re.I)
                for service in campaign.services
            ):
                active = False
            if signal == "no_website":
                active = company.has_website == "false" and bool(evidence and value is True)
            if signal == "weak_mobile" and company.has_website == "false":
                active = False
            breakdown.append(
                {
                    "signal": signal,
                    "label": label,
                    "points": getattr(rules, signal) if active else 0,
                    "state": "confirmed"
                    if active
                    else "not observed"
                    if value is False
                    else "unknown",
                    "source_url": f"/leads?campaign={campaign_id}"
                    if signal == "industry"
                    else evidence.source_url
                    if evidence
                    else "",
                    "evidence_id": evidence.id if evidence else "",
                    "observed_at": evidence.observed_at.isoformat() if evidence else "",
                }
            )
        points = max(0, min(10, sum(int(item["points"]) for item in breakdown)))
        band = (
            "Contact now"
            if points >= rules.contact_now
            else "Later"
            if points >= rules.later
            else "Skip"
        )
        previous = session.scalar(
            select(LeadScore)
            .where(LeadScore.company_id == company_id, LeadScore.campaign_id == campaign_id)
            .order_by(LeadScore.created_at.desc(), LeadScore.id.desc())
            .limit(1)
        )
        if previous and previous.breakdown == breakdown and previous.policy_version == version:
            return previous
        old = {item["signal"]: item["points"] for item in previous.breakdown} if previous else {}
        changed = [
            f"{item['points'] - old.get(item['signal'], 0):+d} {item['label']}"
            for item in breakdown
            if item["points"] != old.get(item["signal"], 0)
        ]
        reason = "; ".join(changed) or (
            "Evidence or scoring policy updated"
            if previous
            else "Initial score; unconfirmed signals add zero"
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                [version, breakdown, previous.id if previous else ""], sort_keys=True
            ).encode()
        ).hexdigest()
        item = LeadScore(
            company_id=company_id,
            campaign_id=campaign_id,
            policy_version=version,
            points=points,
            band=band,
            breakdown=breakdown,
            reason=reason,
            fingerprint=fingerprint,
        )
        session.add(item)
        session.flush()
        emit(
            session,
            company_id,
            "score:" + item.id,
            "scored",
            {
                "points": points,
                "band": band,
                "reason": reason,
                "score_id": item.id,
                "campaign_id": campaign_id,
            },
            at=item.created_at,
        )
        return item
