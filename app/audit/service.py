"""Bounded website research with robots checks, provenance and a 30-day cache."""

import hashlib
import time
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.extract import extract
from app.core.clock import utc_now
from app.db.lead_models import Company, CompanyDomain, Contact
from app.db.models import Job
from app.db.research_models import Evidence, WebsiteAudit
from app.db.session import Database
from app.integrations.public_http import USER_AGENT, Page, PublicFetchError, fetch_public
from app.sources.validation import domain_of, website_url


def research(
    db: Database,
    job: Job,
    *,
    fetcher: Callable[[str], Page] = fetch_public,
    pause: Callable[[float], None] = time.sleep,
) -> str:
    """Read at most three same-origin pages and persist an idempotent evidence bundle."""
    company_id = str(job.payload["company_id"])
    with db.sessions() as session:
        existing = session.scalar(select(WebsiteAudit).where(WebsiteAudit.job_id == job.id))
        if existing:
            return existing.summary
        company = session.get(Company, company_id)
        if not company or not company.website:
            raise ValueError("Confirm a business website first")
        url = company.website
        match = session.scalar(
            select(CompanyDomain).where(
                CompanyDomain.company_id == company_id,
                CompanyDomain.domain == domain_of(url),
                CompanyDomain.status == "verified",
            )
        )
        if not match:
            raise ValueError("Website identity has not been confirmed")
        recent = session.scalar(
            select(WebsiteAudit)
            .where(
                WebsiteAudit.company_id == company_id,
                WebsiteAudit.website == url,
                WebsiteAudit.created_at > utc_now() - timedelta(days=30),
                WebsiteAudit.state == "complete",
            )
            .order_by(WebsiteAudit.created_at.desc())
            .limit(1)
        )
        if recent and not job.payload.get("force"):
            return "Using the existing audit, less than 30 days old."

    def read_page(target: str, delay: float) -> Page:
        """Retry a read-only transient failure once, with spacing and no hidden loop."""
        for attempt in range(2):
            pause(delay * (attempt + 1))
            try:
                response = fetcher(target)
                if response.status in {429, 500, 502, 503, 504} and attempt == 0:
                    continue
                return response
            except PublicFetchError as error:
                if not error.temporary or attempt:
                    raise
        raise PublicFetchError("Research temporarily unavailable")

    pages: list[Page] = []
    state, summary = "complete", ""
    try:
        robots_url = urljoin(url, "/robots.txt")
        robots = read_page(robots_url, 2)
        parser = RobotFileParser()
        if robots.status in {404, 410}:
            parser.parse([])
        elif robots.status == 200:
            parser.parse(robots.body.splitlines())
        else:
            raise PublicFetchError("Robots policy unavailable; research held")
        delay = max(2, int(parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*") or 0))
        if delay > 10:
            raise PublicFetchError("Site requests a longer crawl delay; manual review required")
        pending = [url]
        for index in range(3):
            if index >= len(pending):
                break
            target = pending[index]
            for hop in range(3):
                if not parser.can_fetch(USER_AGENT, target):
                    raise PublicFetchError("Robots policy does not allow this research page")
                page = read_page(target, delay)
                if page.status in {301, 302, 303, 307, 308}:
                    destination = website_url(urljoin(target, page.location))
                    if hop == 2 or urlsplit(destination).netloc != urlsplit(url).netloc:
                        raise PublicFetchError("Redirect needs a reviewed website match")
                    target = destination
                    continue
                if page.status != 200 or "html" not in page.content_type:
                    raise PublicFetchError("Research page is unavailable or not HTML")
                pages.append(page)
                if index == 0:
                    _, links = extract(page.url, page.body)
                    pending.extend(links)
                break
    except (PublicFetchError, ValueError) as error:
        state = "partial" if pages else "blocked"
        # All messages here originate from controlled adapters, not response bodies.
        summary = str(error)[:300]
    summary = f"{len(pages)} page(s) recorded. " + (
        summary or "Social activity and visual quality need review."
    )
    try:
        with db.sessions.begin() as session:
            current = session.get(Company, company_id)
            if not current or current.website != url:
                raise ValueError("Website changed while research was running; queue a new audit")
            audit = WebsiteAudit(
                company_id=company_id, job_id=job.id, website=url, state=state, summary=summary
            )
            session.add(audit)
            session.flush()
            emails = set(
                session.scalars(select(Contact.email).where(Contact.company_id == company_id))
            )
            for page in pages:
                facts, _ = extract(page.url, page.body)
                session.add(
                    Evidence(
                        company_id=company_id,
                        audit_id=audit.id,
                        kind="page",
                        source_url=page.url,
                        facts=facts,
                        content_hash=hashlib.sha256(page.body.encode()).hexdigest(),
                    )
                )
                for email in facts["emails"]:
                    if email not in emails:
                        session.add(Contact(company_id=company_id, email=email))
                        emails.add(email)
    except IntegrityError:
        with db.sessions() as session:
            previous = session.scalar(select(WebsiteAudit).where(WebsiteAudit.job_id == job.id))
            if previous is None:
                raise
            return previous.summary
    return summary
