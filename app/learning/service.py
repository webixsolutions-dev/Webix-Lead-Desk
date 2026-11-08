"""Apply reviewed suggestions and roll back guidance by appending a new version."""

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.db.learning_models import LearningProposal
from app.db.models import AuditEvent, User
from app.db.outreach_models import PlaybookVersion
from app.db.session import Database
from app.playbook.service import append_version


def apply_proposal(
    db: Database, proposal_id: str, rule: str, expected_version: int, actor_id: str
) -> PlaybookVersion:
    """Atomically add the exact owner-reviewed rule and mark its proposal applied once."""
    if not 10 <= len(rule.strip()) <= 6000:
        raise ValueError("Review a specific rule or example of 10–6,000 characters")
    try:
        with db.sessions.begin() as session:
            proposal = session.get(LearningProposal, proposal_id)
            if not proposal or proposal.state != "pending":
                raise ValueError("This proposal was already reviewed or is missing")
            latest = session.scalar(
                select(PlaybookVersion)
                .where(PlaybookVersion.scope == proposal.scope)
                .order_by(PlaybookVersion.version.desc())
                .limit(1)
            )
            guidance = (latest.guidance + "\n\n" if latest else "") + rule.strip()
            version = append_version(session, proposal.scope, guidance, expected_version, actor_id)
            changed = session.scalar(
                update(LearningProposal)
                .where(LearningProposal.id == proposal_id, LearningProposal.state == "pending")
                .values(
                    state="applied",
                    reviewed_by=actor_id,
                    reviewed_at=utc_now(),
                    applied_version_id=version.id,
                )
                .returning(LearningProposal.id)
            )
            if not changed:
                raise ValueError("Another reviewer already applied this proposal")
            session.add(
                AuditEvent(
                    actor_id=actor_id,
                    action="learning.applied",
                    entity_id=proposal_id,
                    details={"version_id": version.id},
                )
            )
            return version
    except IntegrityError as error:
        raise ValueError("Guidance changed concurrently; reload before applying") from error


def dismiss_proposal(db: Database, proposal_id: str, actor_id: str) -> None:
    """Dismiss a suggestion without changing existing guidance or erasing its evidence."""
    with db.sessions.begin() as session:
        actor = session.get(User, actor_id)
        if not actor or not actor.active or actor.role != "owner":
            raise ValueError("Only the owner can review learning proposals")
        changed = session.scalar(
            update(LearningProposal)
            .where(LearningProposal.id == proposal_id, LearningProposal.state == "pending")
            .values(state="dismissed", reviewed_by=actor_id, reviewed_at=utc_now())
            .returning(LearningProposal.id)
        )
        if not changed:
            raise ValueError("Proposal changed; reload")
        session.add(
            AuditEvent(actor_id=actor_id, action="learning.dismissed", entity_id=proposal_id)
        )


def rollback(
    db: Database, version_id: str, expected_version: int, actor_id: str
) -> PlaybookVersion:
    """Publish prior guidance as a new version, preserving old drafts and full history."""
    try:
        with db.sessions.begin() as session:
            previous = session.get(PlaybookVersion, version_id)
            if not previous or previous.version >= expected_version:
                raise ValueError("Choose an earlier guidance version")
            version = append_version(
                session, previous.scope, previous.guidance, expected_version, actor_id
            )
            session.add(
                AuditEvent(
                    actor_id=actor_id,
                    action="playbook.rollback",
                    entity_id=version.id,
                    details={"restored_from": previous.id},
                )
            )
            return version
    except IntegrityError as error:
        raise ValueError("Guidance changed concurrently; reload before restoring") from error
