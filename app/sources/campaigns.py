"""Owner-maintained targeting while historical import snapshots remain unchanged."""

from sqlalchemy import update

from app.db.lead_models import Campaign
from app.db.models import AuditEvent, User
from app.db.session import Database


def edit_campaign(
    db: Database,
    campaign_id: str,
    expected_version: int,
    actor_id: str,
    *,
    name: str,
    industry: str,
    area: str,
    country: str,
    services: list[str],
    daily_limit: int,
    active: bool,
) -> None:
    """Apply a version-checked targeting edit without changing existing conversation routing."""
    if (
        not all(1 <= len(v.strip()) <= 120 for v in [name, industry, area])
        or country not in {"CA", "US"}
        or not 1 <= len(services) <= 12
        or any(not 1 <= len(v.strip()) <= 120 for v in services)
    ):
        raise ValueError("Complete the campaign name, sector, area, country and services")
    with db.sessions.begin() as session:
        actor, campaign = session.get(User, actor_id), session.get(Campaign, campaign_id)
        if not actor or not actor.active or actor.role != "owner" or not campaign:
            raise ValueError("An active owner must edit an existing campaign")
        if not 1 <= daily_limit <= (5 if campaign.lane == "partner" else 50):
            raise ValueError("Use a daily limit of 1–5 for partners or 1–50 for clients")
        changed = session.scalar(
            update(Campaign)
            .where(Campaign.id == campaign_id, Campaign.version == expected_version)
            .values(
                name=name.strip(),
                industry=industry.strip(),
                area=area.strip(),
                country=country,
                services=[v.strip() for v in services],
                daily_limit=daily_limit,
                active=active,
                version=Campaign.version + 1,
            )
            .returning(Campaign.id)
        )
        if not changed:
            raise ValueError("Campaign changed in another tab; reload before saving")
        session.add(AuditEvent(actor_id=actor_id, action="campaign.updated", entity_id=campaign_id))
