"""Keep requested human responses separate from restarted cold sequences."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.acquisition_models import CallRecord
from app.db.linkedin_models import CompanyCoordination
from app.db.outreach_models import Draft
from app.db.research_models import Evidence


def requested_response(session: Session, draft: Draft) -> bool:
    """Recognize a reply/concept or a first email explicitly requested during a call."""
    if draft.kind in {"concept", "reply"}:
        return True
    if draft.kind != "first_touch":
        return False
    company_id = str(draft.snapshot["company_id"])
    state = session.get(CompanyCoordination, company_id)
    if not state or state.state != "replied" or state.reason != "phone: Interested":
        return False
    call = session.scalar(
        select(CallRecord)
        .where(CallRecord.company_id == company_id)
        .order_by(CallRecord.created_at.desc())
        .limit(1)
    )
    if (
        not call
        or call.outcome != "interested"
        or not call.consent_note
        or call.contact_id != draft.snapshot["contact_id"]
        or draft.created_at < call.created_at
    ):
        return False
    return any(
        evidence.facts.get("call_id") == call.id
        for evidence in session.scalars(
            select(Evidence).where(
                Evidence.company_id == company_id,
                Evidence.kind == "human_call",
                Evidence.id.in_(draft.snapshot["evidence_ids"]),
            )
        )
    )
