"""Shared posting authorization independent of UI controls."""

from sqlalchemy.orm import Session

from app.db.linkedin_models import LinkedInProfile
from app.linkedin.identity import require_staff


def profile_access(
    session: Session, profile_id: str, actor: str, *, own: bool = False
) -> LinkedInProfile:
    """Return an active profile only to its own member or an explicitly allowed owner."""
    user = require_staff(session, actor)
    profile = session.get(LinkedInProfile, profile_id)
    if (
        not profile
        or profile.status != "active"
        or (profile.user_id != actor and (own or user.role != "owner"))
    ):
        raise ValueError("Member profile unavailable")
    require_staff(session, profile.user_id)
    return profile
