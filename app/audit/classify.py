"""Evidence-referenced service hypotheses, independent of model or provider."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.research_models import Evidence


class Finding(BaseModel):
    """One observation explicitly tied to stored source evidence."""

    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    observation: str = Field(min_length=1, max_length=400)


class Recommendation(BaseModel):
    """A non-binding hypothesis that can abstain rather than inventing a need."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["recommend", "abstain"]
    service: str = Field(max_length=120)
    score: int = Field(ge=1, le=10)
    reason: str = Field(min_length=10, max_length=1000)
    observed: list[Finding] = Field(max_length=10)
    needs_confirmation: list[str] = Field(max_length=10)
    owner_question: str = Field(min_length=10, max_length=500)


def validate_recommendation(
    result: Recommendation, evidence: list[Evidence], services: list[str]
) -> Recommendation:
    """Reject invented evidence references, unsupported service choices and empty certainty."""
    allowed = {item.id for item in evidence}
    if any(finding.evidence_id not in allowed for finding in result.observed):
        raise ValueError("Recommendation refers to unknown evidence")
    if result.decision == "recommend" and (
        result.service not in services or not result.observed or not result.needs_confirmation
    ):
        raise ValueError("Select one enabled service and include evidence and an uncertainty")
    if result.decision == "abstain" and result.service:
        raise ValueError("An abstention cannot select a service")
    return result
