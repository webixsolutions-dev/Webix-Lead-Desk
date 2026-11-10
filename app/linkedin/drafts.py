"""Exact owner approval is required before a human task can expose outgoing copy."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.lead_models import Company
from app.db.linkedin_draft_models import LinkedInApproval, LinkedInDraft
from app.db.linkedin_models import (
    CompanyCoordination,
    LinkedInProfile,
    LinkedInTask,
    Person,
    PersonOwnership,
)
from app.db.models import AuditEvent, User
from app.db.outreach_models import Mailbox
from app.db.research_models import Evidence
from app.db.session import Database
from app.drafting.check_context import linkedin_report, stale_check
from app.drafting.checks import require_override
from app.drafting.content import fingerprint
from app.linkedin.content import identity, validate_basis, validate_copy
from app.linkedin.coordination import lock_company, outreach_block
from app.linkedin.identity import require_staff


def latest(session: Session, task_id: str) -> LinkedInDraft | None:
    """Return the newest revision, including an unapproved edit."""
    return session.scalar(
        select(LinkedInDraft)
        .where(LinkedInDraft.task_id == task_id)
        .order_by(LinkedInDraft.revision.desc())
        .limit(1)
    )


def valid(session: Session, task: LinkedInTask, now: datetime | None = None) -> bool:
    """Recheck exact approval, recipient, sender and contact evidence before any action."""
    draft = latest(session, task.id)
    if not draft or draft.state != "approved" or draft.digest != task.approval_digest:
        return False
    approval = session.get(LinkedInApproval, draft.id)
    owner = session.get(User, approval.actor_id) if approval else None
    if (
        not approval
        or approval.digest != draft.digest
        or not owner
        or not owner.active
        or owner.role != "owner"
    ):
        return False
    try:
        check_snapshot(session, task, draft.snapshot, now or utc_now())
    except ValueError:
        return False
    return fingerprint(draft.snapshot) == draft.digest


def check_snapshot(
    session: Session, task: LinkedInTask, data: dict[str, Any], now: datetime
) -> None:
    """Invalidate stale routing, changed identity, expired basis or changed reviewed facts."""
    person = session.get(Person, task.person_id)
    profile = session.get(LinkedInProfile, task.profile_id)
    ownership = session.get(PersonOwnership, task.person_id)
    company = session.get(CompanyCoordination, task.company_id)
    if (
        not person
        or not profile
        or profile.status != "active"
        or not ownership
        or ownership.released_at is not None
        or person.company_id != task.company_id
        or not company
        or company.profile_id != profile.id
        or ownership.profile_id != profile.id
        or (not ownership.active and task.action != "reply")
    ):
        raise ValueError("The member assignment is no longer eligible")
    if not person.linkedin_url or person.presence in {"low", "none"}:
        raise ValueError("Review an active LinkedIn profile before planning a message")
    if any(
        data.get(k) != v
        for k, v in {
            "task_id": task.id,
            "company_id": task.company_id,
            "sender_user_id": profile.user_id,
            "person_id": person.id,
            "profile_id": profile.id,
            "profile_url": profile.profile_url,
            "recipient_url": person.linkedin_url,
            "name": person.full_name,
            "action": task.action,
            "generation": task.generation,
        }.items()
    ):
        raise ValueError("Routing changed; save and approve a fresh revision")
    blocked = outreach_block(session, task.company_id, response=task.action == "reply")
    if blocked:
        raise ValueError(blocked)
    validate_copy(
        task.action,
        data["body"],
        data["noncommercial"],
        allow_price=data.get("allow_price") is True,
    )
    if data.get("conversation_checks") and stale_check(
        data["conversation_checks"], linkedin_report(session, data)
    ):
        raise ValueError("Conversation or offer status changed; save and review a fresh revision")
    if task.action.startswith("message") or task.action == "reply":
        mailbox = session.get(Mailbox, data.get("mailbox_id", ""))
        if not mailbox or identity(profile, mailbox) != data["footer"]:
            raise ValueError("Business identity changed; review a fresh draft")
        validate_basis(data["basis"], now)
        if not data["evidence"]:
            raise ValueError("Select reviewed evidence for a personalized message")
    for item in data["evidence"]:
        fact = session.get(Evidence, item["id"])
        if (
            not fact
            or fact.company_id != task.company_id
            or not fact.reviewed_by
            or fingerprint(fact.facts) != item["digest"]
        ):
            raise ValueError("Reviewed evidence changed; review the draft again")


def save(
    db: Database,
    task_id: str,
    actor: str,
    body: str,
    *,
    mailbox_id: str = "",
    evidence_ids: list[str] | None = None,
    basis: dict[str, Any] | None = None,
    angle: str = "",
    noncommercial: bool = False,
    previous_id: str | None = None,
    allow_price: bool = False,
) -> LinkedInDraft:
    """Create one immutable revision and revoke any previous approval atomically."""
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        task = session.get(LinkedInTask, task_id)
        if not task:
            raise ValueError("Task not found")
        lock_company(session, task.company_id)
        session.refresh(task)
        if task.state in {"acting", "needs_check", "done", "cancelled"}:
            raise ValueError("Resolve this task before editing its copy")
        if allow_price and task.action != "reply":
            raise ValueError("Prices are permitted only in an owner-reviewed reply")
        old = latest(session, task.id)
        if (old.id if old else None) != previous_id:
            raise ValueError("The draft changed; reload before saving")
        person = session.get(Person, task.person_id)
        profile = session.get(LinkedInProfile, task.profile_id)
        assert person and profile
        mailbox = session.get(Mailbox, mailbox_id) if mailbox_id else None
        facts = [session.get(Evidence, key) for key in dict.fromkeys(evidence_ids or [])]
        if len(facts) > 10 or any(
            not fact or fact.company_id != task.company_id or not fact.reviewed_by for fact in facts
        ):
            raise ValueError("Choose up to ten reviewed facts belonging to this business")
        if not 3 <= len(angle.strip()) <= 120:
            raise ValueError("Name the angle so results can be compared later")
        company = session.get(Company, task.company_id)
        ownership = session.get(PersonOwnership, task.person_id)
        data = {
            "measurement": {
                "segment": company.industry if company else "unknown",
                "warm": ownership.warm if ownership else None,
            },
            "task_id": task.id,
            "company_id": task.company_id,
            "sender_user_id": profile.user_id,
            "person_id": person.id,
            "profile_id": profile.id,
            "profile_url": profile.profile_url,
            "recipient_url": person.linkedin_url,
            "name": person.full_name,
            "action": task.action,
            "generation": task.generation,
            "body": body.strip(),
            "allow_price": allow_price,
            "footer": identity(profile, mailbox) if mailbox else "",
            "mailbox_id": mailbox_id,
            "basis": basis or {},
            "angle": angle.strip(),
            "noncommercial": noncommercial,
            "evidence": [
                {"id": fact.id, "digest": fingerprint(fact.facts)} for fact in facts if fact
            ],
        }
        if task.action not in {"invite", "withdraw"}:
            data["conversation_checks"] = linkedin_report(session, data)
        check_snapshot(session, task, data, utc_now())
        if old:
            old.state = "superseded"
        task.approval_digest, task.state, task.reason = None, "pending", "Owner approval required"
        draft = LinkedInDraft(
            task_id=task.id,
            revision=old.revision + 1 if old else 1,
            snapshot=data,
            digest=fingerprint(data),
            created_by=actor,
        )
        session.add(draft)
        session.flush()
        return draft


def approve(
    db: Database, draft_id: str, digest: str, actor: str, *, override_note: str = ""
) -> None:
    """Approve only the currently displayed exact revision, never all future edits."""
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        draft = session.get(LinkedInDraft, draft_id)
        if not draft:
            raise ValueError("Draft not found")
        task = session.get(LinkedInTask, draft.task_id)
        assert task
        lock_company(session, task.company_id)
        session.refresh(task)
        session.refresh(draft)
        current = latest(session, task.id)
        if (
            not current
            or current.id != draft.id
            or draft.state != "draft"
            or task.state not in {"pending", "ready"}
            or draft.digest != digest
            or fingerprint(draft.snapshot) != digest
        ):
            raise ValueError("Draft changed or is no longer eligible; reload before approving")
        check_snapshot(session, task, draft.snapshot, utc_now())
        if task.action not in {"invite", "withdraw"}:
            require_override(linkedin_report(session, draft.snapshot), override_note)
        session.add(LinkedInApproval(draft_id=draft.id, digest=digest, actor_id=actor))
        draft.state, task.approval_digest = "approved", digest
        task.state, task.reason = "ready", "Approved for this member and exact text"
        session.add(
            AuditEvent(
                actor_id=actor,
                action="linkedin.draft_approved",
                entity_id=draft.id,
                details={"conversation_override": override_note.strip()[:1000]},
            )
        )
