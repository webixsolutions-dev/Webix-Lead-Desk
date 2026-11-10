"""Named research work and dated revisits from the latest campaign assessments."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.lead_models import Company
from app.db.research_models import Assessment


def next_actions(session: Session, now: datetime) -> list[dict[str, Any]]:
    """A superseded or future action never becomes today's work."""
    seen: set[tuple[str, str]] = set()
    rows = []
    for assessment in session.scalars(select(Assessment).order_by(Assessment.created_at.desc())):
        key = (assessment.company_id, assessment.campaign_id)
        if key in seen:
            continue
        seen.add(key)
        data = assessment.result
        decision = data.get("decision")
        if decision == "research_more":
            detail = "; ".join(data.get("research_checks", []))
        elif (
            decision == "revisit_later" and data.get("revisit_on", "9999") <= now.date().isoformat()
        ):
            detail = (
                "Revisit due " + str(data.get("revisit_on")) + ": " + str(data.get("reason", ""))
            )
        else:
            continue
        company = session.get(Company, assessment.company_id)
        if company:
            rows.append(
                {
                    "key": "outcome:" + assessment.id,
                    "title": company.name,
                    "detail": detail,
                    "url": "/leads/" + company.id,
                    "channel": "Research",
                    "at": assessment.created_at,
                }
            )
    return rows
