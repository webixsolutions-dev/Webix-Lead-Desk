"""Shared member-company access checks for manual call records and outcomes."""

from sqlalchemy.orm import Session

from app.db.linkedin_models import LinkedInProfile
from app.db.models import AuditEvent, User
from app.db.session import Database
from app.linkedin.coordination import lock_company
from app.linkedin.identity import require_staff


def can_call(session: Session, company_id: str, actor: User) -> bool:
    """Owners see all businesses; members see only their assigned company."""
    from app.db.linkedin_models import CompanyCoordination

    if not actor.active:
        return False
    if actor.role == "owner":
        return True
    assignment = session.get(CompanyCoordination, company_id)
    profile = (
        session.get(LinkedInProfile, assignment.profile_id)
        if assignment and assignment.profile_id
        else None
    )
    return bool(profile and profile.user_id == actor.id)


def assign_calls(db: Database, company_id: str, profile_id: str, actor: str) -> None:
    """Assign a company without overriding a different active member's ownership."""
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        profile = session.get(LinkedInProfile, profile_id)
        if not profile or profile.status != "active":
            raise ValueError("Choose an active real member")
        assignment = lock_company(session, company_id)
        if assignment.profile_id and assignment.profile_id != profile_id:
            raise ValueError("Release the existing company assignment before changing members")
        assignment.profile_id = profile_id
        session.add(AuditEvent(actor_id=actor, action="calls.assigned", entity_id=company_id))
