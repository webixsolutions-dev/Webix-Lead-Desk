"""Conservative official-site identity matching using a pinned public fetcher."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin

from app.audit.extract import Extractor
from app.integrations.public_http import Page, PublicFetchError, fetch_public
from app.sources.enrichment.schema import BusinessResult
from app.sources.validation import domain_of, website_url

DIRECTORIES = {
    "facebook.com",
    "instagram.com",
    "yelp.com",
    "yelp.ca",
    "yellowpages.ca",
    "yellowpages.com",
    "homestars.com",
    "linkedin.com",
    "bbb.org",
    "mapquest.com",
    "google.com",
    "google.ca",
    "maps.app.goo.gl",
    "tripadvisor.ca",
    "tripadvisor.com",
    "canpages.ca",
}


def official_domain(url: str) -> bool:
    """Reject directory and social domains, including their subdomains."""
    host = domain_of(url)
    return bool(host) and not any(host == item or host.endswith("." + item) for item in DIRECTORIES)


def words(value: str) -> str:
    """Normalize visible text for explicit identity comparison, not fuzzy hallucination."""
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


@dataclass(frozen=True)
class Match:
    """Validation result and independently fetched visible first-party evidence."""

    accepted: bool
    reason: str
    text: str = ""
    website: str = ""


def validate_site(
    result: BusinessResult,
    name: str,
    city: str,
    address: str,
    fetcher: Callable[[str], Page] = fetch_public,
) -> Match:
    """Require the business name and locality on a bounded official public page."""
    if not result.website:
        return Match(False, "No official website confirmed; review listing evidence")
    if not official_domain(result.website):
        return Match(False, "Directory/social URL rejected as an official website")
    try:
        url = result.website
        for _ in range(3):
            page = fetcher(url)
            if page.status not in {301, 302, 303, 307, 308}:
                break
            redirected = website_url(urljoin(url, page.location))
            if domain_of(redirected).removeprefix("www.") != domain_of(url).removeprefix("www."):
                return Match(False, "Cross-domain redirect requires review")
            url = redirected
        if page.status != 200 or "html" not in page.content_type:
            return Match(False, "Official site did not return a usable HTML page")
        parser = Extractor(url)
        parser.feed(page.body)
        visible = " ".join(parser.text)[:40_000]
        text = words(visible)
        tokens = [
            token
            for token in words(name).split()
            if token not in {"inc", "ltd", "limited", "corp", "corporation", "the"}
        ]
        if not tokens or not all(
            re.search(r"\b" + re.escape(token) + r"\b", text) for token in tokens
        ):
            return Match(False, "Business name does not match the website")
        if words(city) not in text and (not address or words(address) not in text):
            return Match(False, "City/address does not match the website")
        return Match(True, "Name and locality matched first-party visible text", visible, url)
    except (PublicFetchError, ValueError):
        return Match(False, "Website fetch was blocked or unavailable; reviewer required")
