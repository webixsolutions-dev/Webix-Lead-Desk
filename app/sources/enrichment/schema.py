"""Common bounded provider result with per-field citations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.sources.validation import website_url


class Citation(BaseModel):
    """A supporting source for one specific returned field."""

    model_config = ConfigDict(extra="forbid")
    field: Literal[
        "name",
        "city",
        "address",
        "website",
        "phone",
        "google_maps_url",
        "facebook_url",
        "instagram_url",
        "has_website",
        "rating",
        "review_count",
    ]
    url: str = Field(max_length=2000)

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        """Reject unsafe or empty citation links before rendering."""
        result = website_url(value)
        if not result:
            raise ValueError("Citation requires a public URL")
        return result


class BusinessResult(BaseModel):
    """Candidate facts are evidence, never authority or proof of consent."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=120)
    address: str = Field(max_length=300)
    website: str = Field(max_length=2000)
    phone: str = Field(max_length=60)
    google_maps_url: str = Field(max_length=2000)
    facebook_url: str = Field(max_length=2000)
    instagram_url: str = Field(max_length=2000)
    has_website: Literal["true", "false", "unknown"]
    rating: float | None = Field(ge=0, le=5)
    review_count: int | None = Field(ge=0, le=10_000_000)
    place_id: str = Field(max_length=300)
    sources: list[Citation] = Field(max_length=30)

    @field_validator("website", "google_maps_url", "facebook_url", "instagram_url")
    @classmethod
    def public_url(cls, value: str) -> str:
        """Normalize all outbound links with the shared safe URL parser."""
        return website_url(value)


class SearchResults(BaseModel):
    """One bounded search result, with no unbounded pagination loop."""

    model_config = ConfigDict(extra="forbid")
    businesses: list[BusinessResult] = Field(max_length=10)
