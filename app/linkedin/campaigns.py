"""Independent LinkedIn targeting using the shared campaign directory."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.preferences import SettingsConflict
from app.db.lead_models import Campaign, CampaignLead
from app.db.linkedin_models import LinkedInProfile, Person, PersonOwnership
from app.db.models import AuditEvent, User, WorkspaceSetting
from app.db.session import Database
from app.linkedin.identity import assign_person, require_staff
from app.sources.campaigns import ARCHIVED_CAMPAIGN_PREFIX, archived_campaign_ids

PREFIX = "linkedin_campaign:"


def active_campaign(session: Session, campaign_id: str) -> WorkspaceSetting | None:
    """Return a usable routing rule, independent of the email campaign switch."""
    rule = session.get(WorkspaceSetting, PREFIX + campaign_id)
    if not rule or not rule.value.get("active"):
        return None
    archive = session.get(WorkspaceSetting, ARCHIVED_CAMPAIGN_PREFIX + campaign_id)
    if archive and archive.value.get("archived"):
        return None
    profile = session.get(LinkedInProfile, rule.value.get("profile_id", ""))
    user = session.get(User, profile.user_id) if profile else None
    return rule if profile and profile.status == "active" and user and user.active else None


def save_routing(
    db: Database, campaign_id: str, profile_id: str, active: bool, version: int, actor_id: str
) -> None:
    """Save one owner-selected profile per industry campaign."""
    try:
        with db.sessions.begin() as session:
            require_staff(session, actor_id, owner=True)
            campaign = session.get(Campaign, campaign_id, with_for_update=True)
            if not campaign or campaign_id in archived_campaign_ids(session):
                raise ValueError("Choose a campaign in the active directory.")
            key = PREFIX + campaign_id
            current = session.get(WorkspaceSetting, key, with_for_update=True)
            if version != (current.version if current else 0):
                raise SettingsConflict(
                    "LinkedIn targeting changed in another tab. Reload to continue."
                )
            profile = session.get(LinkedInProfile, profile_id) if profile_id else None
            if active or profile_id:
                if not profile or profile.status != "active":
                    raise ValueError("Choose an active LinkedIn team profile.")
                require_staff(session, profile.user_id)
            if (
                current
                and profile_id != current.value.get("profile_id")
                and session.scalar(
                    select(PersonOwnership.person_id)
                    .where(
                        PersonOwnership.active.is_(True),
                        PersonOwnership.reason == f"LinkedIn campaign {campaign_id}",
                    )
                    .limit(1)
                )
            ):
                raise ValueError(
                    "Release existing LinkedIn assignments before changing this campaign's profile."
                )
            value: dict[str, Any] = {
                "profile_id": profile_id,
                "active": active,
                "actor_id": actor_id,
            }
            if current:
                current.value = value
                current.version += 1
            else:
                session.add(WorkspaceSetting(key=key, value=value))
            session.add(
                AuditEvent(
                    actor_id=actor_id, action="linkedin.campaign_saved", entity_id=campaign_id
                )
            )
    except IntegrityError as error:
        raise SettingsConflict(
            "LinkedIn targeting changed in another tab. Reload to continue."
        ) from error


def route_people(db: Database) -> None:
    """Assign confirmed decision-makers to the selected profile before daily planning."""
    with db.sessions() as session:
        rules = {}
        for key in session.scalars(
            select(WorkspaceSetting.key).where(WorkspaceSetting.key.startswith(PREFIX))
        ):
            campaign_id = key.removeprefix(PREFIX)
            rule = active_campaign(session, campaign_id)
            if rule:
                rules[campaign_id] = (rule.value["profile_id"], rule.value["actor_id"])
        if not rules:
            return
        candidates = list(
            session.execute(
                select(Person.id, CampaignLead.campaign_id)
                .join(CampaignLead, CampaignLead.company_id == Person.company_id)
                .outerjoin(PersonOwnership, PersonOwnership.person_id == Person.id)
                .where(
                    CampaignLead.campaign_id.in_(rules),
                    PersonOwnership.person_id.is_(None),
                    Person.decision_maker.is_(True),
                    Person.linkedin_url.is_not(None),
                    Person.presence.not_in(["low", "none"]),
                )
                .order_by(Person.verified_at, Person.id, CampaignLead.campaign_id)
            )
        )
    for person_id, campaign_id in candidates:
        profile_id, actor_id = rules[campaign_id]
        try:
            assign_person(db, person_id, profile_id, actor_id, campaign_id=campaign_id)
        except ValueError:
            continue  # Existing ownership and global outreach stops take precedence.


def ownership_active(session: Session, ownership: PersonOwnership) -> bool:
    """Manual assignments have no campaign rule; managed ones honor pause and profile changes."""
    if not ownership.reason.startswith("LinkedIn campaign "):
        return True
    rule = active_campaign(session, ownership.reason.removeprefix("LinkedIn campaign "))
    return bool(rule and rule.value["profile_id"] == ownership.profile_id)
