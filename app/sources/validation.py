"""Normalize bounded import values without treating websites as trusted code."""

import hashlib
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def website_url(value: str) -> str:
    """Accept a public HTTP(S) hostname; fetch-time DNS validation is separate."""
    value = value.strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    if "\\" in value or any(ord(c) < 33 for c in value):
        raise ValueError("Ambiguous URL characters are not accepted")
    parts = urlsplit(value)
    host = (parts.hostname or "").encode("idna").decode().lower().rstrip(".")
    if (
        parts.scheme not in {"https", "http"}
        or not host
        or not re.fullmatch(r"[a-z0-9.-]+", host)
        or any(
            not label or label.startswith("-") or label.endswith("-") for label in host.split(".")
        )
        or parts.username
        or parts.password
        or parts.port not in {None, 443 if parts.scheme == "https" else 80}
        or len(value) > 2000
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError("Use a public HTTP(S) website without credentials or a custom port")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith(
            (".local", ".localhost", ".internal", ".test", ".example")
        ):
            raise ValueError("Use a public website hostname") from None
    else:
        raise ValueError("IP-address websites are not accepted")
    return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, ""))


def domain_of(value: str) -> str:
    """Return a normalized hostname without conflating distinct registered sites."""
    return (urlsplit(website_url(value)).hostname or "").removeprefix("www.")


def email_address(value: str) -> str:
    """Normalize a single address; syntax validity is not deliverability or consent."""
    value = value.strip().casefold()
    if value and (
        len(value) > 254
        or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}", value)
    ):
        raise ValueError("Use one valid email address")
    return value


def identity_key(name: str, city: str, address: str) -> str:
    """Build a deterministic identity from a business name and location, not its domain."""
    pieces = [re.sub(r"\s+", " ", part.strip().casefold()) for part in (name, city, address)]
    return hashlib.sha256("\n".join(pieces).encode()).hexdigest()


class Candidate(BaseModel):
    """One validated input row, before any persistence or outreach eligibility."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=120)
    industry: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=300)
    website: str = ""
    email: str = ""
    phone: str = Field(default="", max_length=60)
    source_key: str = Field(default="", max_length=200)
    source_url: str = ""

    @field_validator("website", "source_url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        """Validate URL fields without fetching them."""
        return website_url(value)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        """Keep address normalization consistent with global suppression."""
        return email_address(value)
