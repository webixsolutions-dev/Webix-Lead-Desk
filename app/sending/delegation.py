"""Versioned owner delegation for new cold outreach, never conversations or quotations."""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.delivery_models import InboxMessage, SendIntent
from app.db.lead_models import Campaign
from app.db.linkedin_models import Touch
from app.db.models import AuditEvent, User, WorkspaceSetting
from app.db.outreach_models import Conversation, Draft, DraftApproval, Offer
from app.db.research_models import Assessment, Evidence
from app.db.session import Database
from app.drafting.content import fingerprint
from app.policy.verification import verify_address

KEY = "outreach_approval_policy"


def read_policy(session: Session) -> tuple[dict[str, Any], int]:
    """Default to exact owner review for every message."""
    row = session.get(WorkspaceSetting, KEY)
    return (
        (row.value, row.version)
        if row
        else ({"mode": "manual", "campaigns": [], "daily_limit": 10}, 0)
    )


def save_policy(
    db: Database, actor_id: str, expected: int, mode: str, campaigns: list[str], daily_limit: int
) -> None:
    """Publish scoped delegation; disabling also invalidates outstanding policy approvals."""
    if mode not in {"manual", "initial", "outreach"} or not 1 <= daily_limit <= 100:
        raise ValueError("Choose a supported mode and a daily automatic limit of 1–100")
    with db.sessions.begin() as session:
        actor = session.get(User, actor_id)
        if not actor or not actor.active or actor.role != "owner":
            raise ValueError("Only the owner can change outreach approval")
        if mode != "manual" and (
            not campaigns
            or set(campaigns)
            != set(session.scalars(select(Campaign.id).where(Campaign.id.in_(campaigns))))
        ):
            raise ValueError("Choose existing campaigns for delegated outreach")
        value = {
            "mode": mode,
            "campaigns": sorted(set(campaigns)),
            "daily_limit": daily_limit,
            "owner_id": actor_id,
            "enabled_at": utc_now().isoformat(),
        }
        row = session.get(WorkspaceSetting, KEY)
        if (row.version if row else 0) != expected:
            raise ValueError("Approval settings changed; reload before saving")
        if row:
            changed = cast(
                CursorResult[Any],
                session.execute(
                    update(WorkspaceSetting)
                    .where(WorkspaceSetting.key == KEY, WorkspaceSetting.version == expected)
                    .values(value=value, version=expected + 1, updated_at=utc_now())
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("Approval settings changed; reload before saving")
        else:
            session.add(WorkspaceSetting(key=KEY, value=value, version=1))
        # Revoke untransmitted automatic approvals so the owner can review them normally.
        revoked = list(
            session.scalars(
                select(DraftApproval.draft_id)
                .join(Draft, Draft.id == DraftApproval.draft_id)
                .where(DraftApproval.source == "policy", Draft.state == "approved")
            )
        )
        if revoked:
            session.execute(update(Draft).where(Draft.id.in_(revoked)).values(state="draft"))
            session.execute(delete(DraftApproval).where(DraftApproval.draft_id.in_(revoked)))
            session.execute(
                update(SendIntent)
                .where(SendIntent.draft_id.in_(revoked), SendIntent.attempts == 0)
                .values(state="held", hold_reason="automatic_approval_revoked")
            )
        session.add(
            AuditEvent(
                actor_id=actor_id,
                action="outreach.policy_changed",
                details={"mode": mode, "version": expected + 1},
            )
        )


def policy_block(session: Session, draft: Draft, version: int | None) -> str | None:
    """Revalidate delegation at approval, scheduling and immediately before transmission."""
    policy, current = read_policy(session)
    if policy.get("mode") == "manual" or current != version:
        return "automatic_approval_revoked"
    if draft.kind not in (
        {"first_touch", "follow_up"} if policy.get("mode") == "outreach" else {"first_touch"}
    ):
        return "reply_requires_owner_approval"
    if draft.snapshot.get("allow_price") or draft.snapshot.get("attachments"):
        return "commercial_response_requires_owner_approval"
    if draft.snapshot.get("campaign_id") not in policy.get("campaigns", []):
        return "campaign_not_delegated"
    if draft.created_at < datetime.fromisoformat(policy["enabled_at"]):
        return "existing_draft_requires_owner_approval"
    conversation = session.get(Conversation, draft.conversation_id)
    if not conversation or conversation.state in {"held", "stopped", "replied"}:
        return "conversation_requires_owner_approval"
    if session.scalar(
        select(InboxMessage.id).where(InboxMessage.conversation_id == conversation.id).limit(1)
    ) or session.scalar(
        select(Touch.id)
        .where(Touch.company_id == conversation.company_id, Touch.direction == "inbound")
        .limit(1)
    ):
        return "incoming_response_requires_owner_approval"
    owner = session.get(User, policy.get("owner_id"))
    if not owner or not owner.active or owner.role != "owner":
        return "delegating_owner_unavailable"
    campaign = session.get(Campaign, conversation.campaign_id)
    assessment = session.scalar(
        select(Assessment)
        .where(
            Assessment.company_id == conversation.company_id,
            Assessment.campaign_id == conversation.campaign_id,
        )
        .order_by(Assessment.created_at.desc())
        .limit(1)
    )
    service = assessment.result.get("service") if assessment else None
    if (
        not campaign
        or not assessment
        or assessment.result.get("decision") != "recommend"
        or service not in campaign.services
        or not session.scalar(
            select(Offer.id).where(Offer.active.is_(True), Offer.service == service)
        )
    ):
        return "available_service_recommendation_required"
    return None


def approve_automatically(db: Database, draft_id: str) -> str:
    """Apply delegation to one exact new revision with recorded evidence and assessment."""
    from app.drafting.service import approval_blocks

    with db.sessions() as session:
        draft = session.get(Draft, draft_id)
        if not draft or draft.state != "draft":
            raise ValueError("Draft changed")
        policy, version = read_policy(session)
        if reason := policy_block(session, draft, version):
            raise ValueError(reason)
    verify_address(db, draft.snapshot["contact_id"])
    if reasons := approval_blocks(db, draft):
        raise ValueError("; ".join(reasons))
    with db.sessions.begin() as session:
        # Updating the singleton row serializes allowance checks against settings changes.
        if (
            cast(
                CursorResult[Any],
                session.execute(
                    update(WorkspaceSetting)
                    .where(WorkspaceSetting.key == KEY, WorkspaceSetting.version == version)
                    .values(updated_at=utc_now())
                ),
            ).rowcount
            != 1
        ):
            raise ValueError("Delegation changed")
        session.expire_all()
        draft = session.get(Draft, draft_id)
        assert draft
        if reason := policy_block(session, draft, version):
            raise ValueError(reason)
        evidence = list(
            session.scalars(select(Evidence).where(Evidence.id.in_(draft.snapshot["evidence_ids"])))
        )
        assessment = session.scalar(
            select(Assessment)
            .where(
                Assessment.company_id == draft.snapshot["company_id"],
                Assessment.campaign_id == draft.snapshot["campaign_id"],
            )
            .order_by(Assessment.created_at.desc())
            .limit(1)
        )
        if not evidence or not assessment or assessment.result.get("decision") != "recommend":
            raise ValueError("A supported service recommendation and source evidence are required")
        start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        count = (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "draft.policy_approved", AuditEvent.created_at >= start)
            )
            or 0
        )
        if count >= policy["daily_limit"]:
            raise ValueError("Automatic daily approval limit reached")
        if (
            draft.digest != fingerprint(draft.snapshot)
            or cast(
                CursorResult[Any],
                session.execute(
                    update(Draft)
                    .where(
                        Draft.id == draft.id, Draft.state == "draft", Draft.digest == draft.digest
                    )
                    .values(state="approved")
                ),
            ).rowcount
            != 1
        ):
            raise ValueError("Draft changed during automatic review")
        session.add(
            DraftApproval(
                draft_id=draft.id,
                digest=draft.digest,
                approved_by=policy["owner_id"],
                source="policy",
                policy_version=version,
            )
        )
        session.add(
            AuditEvent(
                actor_id=policy["owner_id"],
                action="draft.policy_approved",
                entity_id=draft.id,
                details={"policy_version": version},
            )
        )
        return str(policy["owner_id"])


def tick(db: Database, settings: Settings) -> None:
    """Schedule bounded delegated drafts only on an explicitly enabled delivery worker."""
    if not settings.live_send_enabled or settings.app_mode == "VERCEL":
        return
    from app.sending.schedule import schedule

    with db.sessions() as session:
        policy, _ = read_policy(session)
        if policy.get("mode") == "manual":
            return
        items = list(
            session.scalars(
                select(Draft)
                .where(Draft.state.in_(["draft", "approved"]))
                .order_by(Draft.created_at)
                .limit(100)
            )
        )
    for draft in items:
        try:
            if draft.state == "draft":
                owner_id = approve_automatically(db, draft.id)
            else:
                with db.sessions() as session:
                    approval = session.get(DraftApproval, draft.id)
                    if (
                        not approval
                        or approval.source != "policy"
                        or session.scalar(
                            select(SendIntent.id).where(SendIntent.draft_id == draft.id)
                        )
                    ):
                        continue
                    owner_id = approval.approved_by
            schedule(db, draft.id, owner_id)
        except ValueError:
            continue
