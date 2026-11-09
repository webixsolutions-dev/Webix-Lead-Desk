"""Member-scoped human planning views; planning is not approval or execution."""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Person
from app.db.models import User
from app.db.session import Database
from app.sending.window import CALGARY


def planned_tasks(db: Database, actor: User, now: datetime) -> list[dict[str, Any]]:
    """Expose all plans to the owner and only the signed-in member's own queue to staff."""
    with db.sessions() as session:
        query = (
            select(LinkedInTask, Person, LinkedInProfile)
            .join(Person, Person.id == LinkedInTask.person_id)
            .join(LinkedInProfile, LinkedInProfile.id == LinkedInTask.profile_id)
            .where(LinkedInTask.state.not_in(["done", "cancelled"]))
        )
        if actor.role != "owner":
            query = query.where(LinkedInProfile.user_id == actor.id)
        return [
            {
                "task": task,
                "person": person,
                "profile": profile,
                "due": bool(task.release_at and task.release_at <= now),
                "release_label": task.release_at.astimezone(CALGARY).strftime("%a %d %b · %H:%M")
                if task.release_at
                else "Waiting for capacity",
            }
            for task, person, profile in session.execute(
                query.order_by(LinkedInTask.due_at, LinkedInTask.created_at).limit(100)
            )
        ]
