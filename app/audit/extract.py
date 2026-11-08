"""Deterministic HTML extraction; script contents and commands are never executed."""

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.sources.validation import email_address, website_url


class Extractor(HTMLParser):
    """Collect bounded visible facts while ignoring executable and hidden elements."""

    def __init__(self, url: str) -> None:
        """Initialize a parser for one source URL."""
        super().__init__(convert_charrefs=True)
        self.url = url
        self.ignored = 0
        self.in_title = False
        self.title: list[str] = []
        self.text: list[str] = []
        self.description = ""
        self.viewport = False
        self.forms = 0
        self.links: list[str] = []
        self.emails: set[str] = set()
        self.phones: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Read HTML attributes as untrusted data with no browser side effects."""
        data = dict(attrs)
        if tag in {"script", "style", "template", "noscript"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            name = (data.get("name") or "").lower()
            if name == "description":
                self.description = (data.get("content") or "")[:500]
            if name == "viewport":
                self.viewport = True
        if tag == "form":
            self.forms += 1
        if tag == "a":
            link = data.get("href") or ""
            if link.lower().startswith("mailto:"):
                try:
                    email = email_address(link[7:].split("?")[0])
                    if email:
                        self.emails.add(email)
                except ValueError:
                    pass  # Invalid markup is not an email candidate.
            elif link.lower().startswith("tel:"):
                self.phones.add(link[4:][:60])
            else:
                try:
                    target = website_url(urljoin(self.url, link))
                    if target and target not in self.links and len(self.links) < 100:
                        self.links.append(target)
                except ValueError:
                    pass  # Non-HTTP and malformed links are excluded.

    def handle_endtag(self, tag: str) -> None:
        """Leave ignored or title elements."""
        if tag in {"script", "style", "template", "noscript"} and self.ignored:
            self.ignored -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        """Keep limited visible text without interpreting embedded instructions."""
        if self.ignored:
            return
        if self.in_title:
            self.title.append(data[:300])
        if len(self.text) < 1000:
            self.text.append(data[:500])

    def facts(self) -> dict[str, Any]:
        """Return observed markup features, not business performance conclusions."""
        visible = re.sub(r"\s+", " ", " ".join(self.text)).strip()[:4000]
        social_hosts = {
            "facebook.com",
            "instagram.com",
            "linkedin.com",
            "youtube.com",
            "tiktok.com",
        }
        social = [
            link
            for link in self.links
            if (urlsplit(link).hostname or "").removeprefix("www.") in social_hosts
        ]
        return {
            "title": " ".join(self.title)[:300],
            "description": self.description,
            "viewport_declared": self.viewport,
            "form_elements": self.forms,
            "emails": sorted(self.emails)[:10],
            "phones": sorted(self.phones)[:10],
            "social_links": social[:10],
            "visible_excerpt": visible,
            "quote_words_present": bool(re.search(r"\b(quote|estimate)\b", visible, re.I)),
            "visual_quality": "unknown",
            "form_delivery": "not tested",
            "traffic": "unknown",
            "ad_activity": "unknown",
            "revenue": "unknown",
        }


def extract(url: str, html: str) -> tuple[dict[str, Any], list[str]]:
    """Extract facts plus up to two same-host service/contact candidates."""
    parser = Extractor(url)
    parser.feed(html)
    candidates: list[str] = []
    for pattern in (r"contact|quote|estimate", r"service|heating|cooling|roof|clean"):
        match = next(
            (
                link
                for link in parser.links
                if link != url
                and link not in candidates
                and urlsplit(link).netloc == urlsplit(url).netloc
                and re.search(pattern, urlsplit(link).path, re.I)
            ),
            None,
        )
        if match:
            candidates.append(match)
    return parser.facts(), candidates
