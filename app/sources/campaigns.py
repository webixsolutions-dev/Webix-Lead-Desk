"""Owner-maintained targeting while historical import snapshots remain unchanged."""

from sqlalchemy import select, update

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
    if (
        not all(1 <= len(v.strip()) <= 120 for v in [name, industry, area])
        or country not in CAMPAIGN_COUNTRIES
        or not 1 <= len(services) <= 12
        or any(not 1 <= len(v.strip()) <= 120 for v in services)
    ):
        raise ValueError("Complete the campaign name, sector, area, country and services")
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
