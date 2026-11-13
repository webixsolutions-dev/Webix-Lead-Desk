"""Owner-maintained targeting while historical import snapshots remain unchanged."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.delivery_models import SendIntent
from app.db.lead_models import Campaign
from app.db.models import AuditEvent, User, WorkerInstance, WorkspaceSetting
from app.db.outreach_models import Conversation, Draft
from app.db.session import Database
from app.sending.campaign_sender import SENDER_KEY_PREFIX, WORKER_CAPABILITY, WORKER_POLICY_VERSION
from app.sending.sequences import validate_days
from app.sending.signatures import CAMPAIGN_SIGNATURE_PREFIX, require_campaign_signature_worker

CAMPAIGN_COUNTRIES = {
    "CA": "Canada",
    "US": "United States",
    "NZ": "New Zealand",
    "AE": "United Arab Emirates",
}
DRAFT_ONLY_COUNTRIES = frozenset({"NZ", "AE"})
ARCHIVED_CAMPAIGN_PREFIX = "campaign_archive:"


def archived_campaign_ids(session: Session) -> set[str]:
    """Return archived campaign IDs for configuration and targeting lists."""
    return {
        item.key.removeprefix(ARCHIVED_CAMPAIGN_PREFIX)
        for item in session.scalars(
            select(WorkspaceSetting).where(
                WorkspaceSetting.key.startswith(ARCHIVED_CAMPAIGN_PREFIX)
            )
        )
        if item.value.get("archived") is True
    }


def campaign_services(value: str) -> list[str]:
    """Parse the service picker and count each selected service once."""
    return list(dict.fromkeys(service.strip() for service in value.split(",") if service.strip()))


def validate_targeting(
    name: str, industry: str, area: str, country: str, services: list[str]
) -> None:
    """Tell the owner which campaign field needs attention."""
    for label, value in (("campaign name", name), ("industry", industry), ("city or region", area)):
        if not 1 <= len(value.strip()) <= 120:
            raise ValueError(f"Enter the {label} (up to 120 characters).")
    if country not in CAMPAIGN_COUNTRIES:
        raise ValueError("Choose a supported campaign country.")
    if not 1 <= len(services) <= 12:
        raise ValueError("Select between 1 and 12 services for this campaign.")
    if any(not 1 <= len(service.strip()) <= 120 for service in services):
        raise ValueError("Keep each service name between 1 and 120 characters.")


def archive_campaign(
    db: Database, campaign_id: str, actor_id: str, *, restore: bool = False
) -> None:
    """Remove a paused campaign from setup lists while retaining its history."""
    from app.linkedin.campaigns import PREFIX as LINKEDIN_CAMPAIGN_PREFIX

    with db.sessions.begin() as session:
        actor = session.get(User, actor_id)
        campaign = session.get(Campaign, campaign_id, with_for_update=True)
        if not actor or not actor.active or actor.role != "owner" or not campaign:
            raise ValueError("Choose an existing campaign as the workspace owner.")
        if campaign.active:
            raise ValueError("Pause the campaign before archiving it.")
        linkedin = session.get(WorkspaceSetting, LINKEDIN_CAMPAIGN_PREFIX + campaign.id)
        if not restore and linkedin and linkedin.value.get("active"):
            raise ValueError("Pause this campaign on LinkedIn before archiving it.")
        key = ARCHIVED_CAMPAIGN_PREFIX + campaign.id
        current = session.get(WorkspaceSetting, key)
        if restore:
            if current:
                current.value = {"archived": False}
            else:
                session.add(WorkspaceSetting(key=key, value={"archived": False}))
            session.add(
                AuditEvent(actor_id=actor_id, action="campaign.restored", entity_id=campaign_id)
            )
        elif not current or not current.value.get("archived"):
            if current:
                current.value = {"archived": True}
            else:
                session.add(WorkspaceSetting(key=key, value={"archived": True}))
            session.add(
                AuditEvent(actor_id=actor_id, action="campaign.archived", entity_id=campaign_id)
            )


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
    followup_days: list[int] | None = None,
) -> None:
    """Apply a version-checked targeting edit without changing existing conversation routing."""
    validate_targeting(name, industry, area, country, services)
    if active and country in DRAFT_ONLY_COUNTRIES:
        raise ValueError(
            f"{CAMPAIGN_COUNTRIES[country]} campaigns must stay paused "
            "until local sending is supported"
        )
    with db.sessions.begin() as session:
        actor = session.get(User, actor_id)
        campaign = session.get(Campaign, campaign_id, with_for_update=True)
        if not actor or not actor.active or actor.role != "owner" or not campaign:
            raise ValueError("An active owner must edit an existing campaign")
        if campaign_id in archived_campaign_ids(session):
            raise ValueError("This campaign is archived.")
        sender = session.get(WorkspaceSetting, SENDER_KEY_PREFIX + campaign_id)
        if active and sender:
            custom_name = bool(sender.value.get("from_name"))
            supported_policies = (
                {WORKER_POLICY_VERSION} if custom_name else {3, WORKER_POLICY_VERSION}
            )
            worker = session.get(WorkerInstance, "primary")
            if (
                not worker
                or (worker.runtime_config or {}).get(WORKER_CAPABILITY) not in supported_policies
            ):
                version = "v0.33" if custom_name else "v0.30.8"
                raise ValueError(f"Start worker {version} or newer before activating this campaign")
        if active:
            signature = session.get(WorkspaceSetting, CAMPAIGN_SIGNATURE_PREFIX + campaign_id)
            if signature and signature.value.get("html"):
                require_campaign_signature_worker(session)
        if not 1 <= daily_limit <= (5 if campaign.lane == "partner" else 50):
            raise ValueError("Use a daily limit of 1–5 for partners or 1–50 for clients")
        if followup_days is not None:
            validate_days(followup_days, campaign.lane)
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
                followup_days=followup_days
                if followup_days is not None
                else campaign.followup_days,
                version=Campaign.version + 1,
            )
            .returning(Campaign.id)
        )
        if not changed:
            raise ValueError("Campaign changed in another tab; reload before saving")
        if not active:
            conversations = select(Conversation.id).where(Conversation.campaign_id == campaign_id)
            session.execute(
                update(SendIntent)
                .where(
                    SendIntent.conversation_id.in_(conversations),
                    SendIntent.state.in_(["scheduled", "held"]),
                )
                .values(state="cancelled", hold_reason="owner_paused_campaign")
            )
            session.execute(
                update(Draft)
                .where(
                    Draft.conversation_id.in_(conversations),
                    Draft.state.in_(["draft", "approved"]),
                )
                .values(state="cancelled")
            )
        session.add(AuditEvent(actor_id=actor_id, action="campaign.updated", entity_id=campaign_id))
