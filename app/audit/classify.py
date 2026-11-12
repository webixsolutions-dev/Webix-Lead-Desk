"""Evidence-referenced service hypotheses, independent of model or provider."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.clock import utc_now
from app.db.research_models import Evidence


class Finding(BaseModel):
    """One observation explicitly tied to stored source evidence."""

    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    observation: str = Field(min_length=1, max_length=400)


class ServiceOption(BaseModel):
    """A ranked possibility with its own evidence and uncertainty, not a promised need."""

    model_config = ConfigDict(extra="forbid")
    service: str = Field(max_length=120)
    fit: Literal["supported", "needs_confirmation", "not_indicated"]
    reason: str = Field(min_length=10, max_length=500)
    evidence_ids: list[str] = Field(max_length=10)
    question: str = Field(max_length=400)


class Recommendation(BaseModel):
    """A non-binding hypothesis that can abstain rather than inventing a need."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal[
        "recommend",
        "abstain",
        "ask_first",
        "research_more",
        "revisit_later",
        "no_clear_opportunity",
    ]
    service: str = Field(max_length=120)
    score: int = Field(ge=1, le=10)
    reason: str = Field(min_length=10, max_length=1000)
    observed: list[Finding] = Field(max_length=10)
    needs_confirmation: list[str] = Field(max_length=10)
    owner_question: str = Field(min_length=10, max_length=500)
    research_checks: list[str] = Field(default_factory=list, max_length=10)
    revisit_on: str = Field(default="", max_length=10)
    alternatives: list[ServiceOption] = Field(default_factory=list, max_length=8)


def validate_recommendation(
    result: Recommendation,
    evidence: list[Evidence],
    services: list[str],
    *,
    discovery_services: list[str] | None = None,
    catalogue_services: list[str] | None = None,
) -> Recommendation:
    """Reject invented evidence references, unsupported service choices and empty certainty."""
    allowed = {item.id for item in evidence}
    discovery = set(discovery_services or [])
    catalogue = set(catalogue_services or []) | set(services)
    if result.decision == "recommend" and result.service in discovery:
        raise ValueError("Discovery-only services need a question, not a delivery recommendation")
    if result.decision == "ask_first" and (result.service not in services or not result.observed):
        raise ValueError("Ask-first needs a campaign service and a recorded observation")
    if result.decision == "research_more" and (
        not result.research_checks
        or any(not 10 <= len(x.strip()) <= 400 for x in result.research_checks)
    ):
        raise ValueError("Name the specific remaining research checks")
    if result.decision == "revisit_later":
        try:
            if date.fromisoformat(result.revisit_on) <= utc_now().date():
                raise ValueError("Choose a future revisit date")
        except ValueError as error:
            raise ValueError("Choose a future revisit date (YYYY-MM-DD)") from error
    elif result.revisit_on:
        raise ValueError("Only revisit-later has a revisit date")
    if any(finding.evidence_id not in allowed for finding in result.observed):
        raise ValueError("Recommendation refers to unknown evidence")
    if result.decision == "recommend" and (
        result.service not in services or not result.observed or not result.needs_confirmation
    ):
        raise ValueError("Select one enabled service and include evidence and an uncertainty")
    for option in result.alternatives:
        if option.service not in catalogue or not set(option.evidence_ids).issubset(allowed):
            raise ValueError(
                "Alternative must use current catalogue services and recorded evidence"
            )
        if option.fit == "supported" and not option.evidence_ids:
            raise ValueError("A supported alternative needs evidence")
        if option.service not in services and (
            option.fit == "not_indicated" or not option.evidence_ids or not option.question.strip()
        ):
            raise ValueError(
                "A service outside the campaign needs evidence and a discovery question"
            )
    if (
        result.decision in {"abstain", "research_more", "revisit_later", "no_clear_opportunity"}
        and result.service
    ):
        raise ValueError("A non-contact outcome cannot select a service")
    return result


def recommendation_schema() -> dict[str, object]:
    """Require the alternatives array on provider output while accepting older stored results."""
    schema = Recommendation.model_json_schema()
    schema["required"] = list(schema["properties"])
    return schema
