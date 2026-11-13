"""Conservative professional identity matching and exclusive member ownership."""

import re
import unicodedata
from datetime import timedelta
from urllib.parse import unquote, urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.lead_models import Company, Contact
from app.db.linkedin_models import (
    LinkedInProfile,
    Person,
    PersonIdentity,
    PersonOwnership,
)
from app.db.models import AuditEvent, User
from app.db.session import Database
from app.journey.events import emit
from app.linkedin.coordination import lock_company, outreach_block
from app.sources.validation import domain_of, email_address, website_url


def linkedin_url(value: str) -> str:
    """Normalize a public member URL; reject lookalikes, credentials and nonprofile routes."""
    parsed = urlsplit(value.strip())
    path = unquote(parsed.path).rstrip("/").lower()
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"linkedin.com", "www.linkedin.com", "ca.linkedin.com"}
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or not re.fullmatch(r"/in/[a-z0-9][a-z0-9_-]{1,180}", path)
    ):
        raise ValueError("Use a public https://www.linkedin.com/in/member profile URL")
    return "https://www.linkedin.com" + path


def require_staff(session: Session, actor: str, *, owner: bool = False) -> User:
    """Require an active staff account for every attributable human decision."""
    user = session.get(User, actor)
    if not user or not user.active or (owner and user.role != "owner"):
        raise ValueError("An active owner is required" if owner else "An active member is required")
    return user


def add_profile(db: Database, user_id: str, url: str, actor: str) -> LinkedInProfile:
    """Owner registers a real member's profile without connecting or impersonating them."""
    url = linkedin_url(url)
    try:
        with db.sessions.begin() as session:
            require_staff(session, actor, owner=True)
            member = require_staff(session, user_id)
            from app.linkedin.quotas import lock_registry

            lock_registry(session)
            if (session.scalar(select(func.count()).select_from(LinkedInProfile)) or 0) >= 10:
                raise ValueError("This workspace supports at most ten real member profiles")
            item = LinkedInProfile(user_id=user_id, display_name=member.name, profile_url=url)
            session.add(item)
            session.flush()
            session.add(
                AuditEvent(actor_id=actor, action="linkedin.profile_added", entity_id=item.id)
            )
            return item
    except IntegrityError as error:
        raise ValueError("This member or profile is already registered") from error


def confirm_person(
    db: Database,
    company_id: str,
    actor: str,
    *,
    name: str,
    role: str,
    url: str,
    email: str = "",
    source_url: str,
    note: str,
    confidence: int = 0,
    presence: str = "unknown",
    decision_maker: bool = False,
) -> Person:
    """Save a human-confirmed match; aliases deduplicate across concurrent requests."""
    name, role, note = name.strip(), role.strip(), note.strip()
    url = linkedin_url(url) if url else ""
    email = email_address(email)
    source_url = website_url(source_url)
    if (
        not 2 <= len(name) <= 160
        or not 2 <= len(role) <= 160
        or not 10 <= len(note) <= 2000
        or not source_url
        or not 0 <= confidence <= 100
        or presence not in {"active", "low", "none", "unknown"}
    ):
        raise ValueError("Confirm the name, business role, source and match explanation")
    if email and email.split("@", 1)[0] in {
        "info",
        "sales",
        "hello",
        "contact",
        "support",
        "office",
    }:
        raise ValueError("A generic company inbox is not a personal identity")
    try:
        with db.sessions.begin() as session:
            require_staff(session, actor)
            company = session.get(Company, company_id)
            if not company:
                raise ValueError("Business not found")
            # The company lock also serializes matching and active-person limits.
            lock_company(session, company_id)
            normalized = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
            domain = domain_of(company.website) if company.website else "company:" + company_id
            keys = {"name:" + normalized + ":" + domain}
            if url:
                keys.add("linkedin:" + url)
            if email:
                keys.add("email:" + email)
            matched = set(
                session.scalars(
                    select(PersonIdentity.person_id).where(PersonIdentity.key.in_(keys))
                )
            )
            if len(matched) > 1:
                raise ValueError(
                    "These identities point to different people; owner review required"
                )
            if matched:
                person = session.get(Person, matched.pop())
                assert person
                if person.company_id != company_id:
                    raise ValueError("This person is already recorded under another business")
                if (url and person.linkedin_url != url) or (
                    email and person.personal_email != email
                ):
                    raise ValueError(
                        "An existing identity differs; review it instead of duplicating it"
                    )
                return person
            contact = (
                session.scalar(
                    select(Contact).where(Contact.company_id == company_id, Contact.email == email)
                )
                if email
                else None
            )
            person = Person(
                company_id=company_id,
                full_name=name,
                role=role,
                linkedin_url=url or None,
                personal_email=email or None,
                contact_id=contact.id if contact else None,
                source_url=source_url,
                verification_note=note,
                verified_by=actor,
                confidence=confidence,
                presence=presence,
                decision_maker=decision_maker,
            )
            session.add(person)
            session.flush()
            session.add_all(PersonIdentity(key=key, person_id=person.id) for key in keys)
            emit(
                session,
                company_id,
                "person:" + person.id,
                "person_verified",
                {"person_id": person.id, "source_url": source_url},
                actor=actor,
            )
            return person
    except IntegrityError as error:
        raise ValueError(
            "This person already exists; reload to review the matching record"
        ) from error


def assign_person(
    db: Database,
    person_id: str,
    profile_id: str,
    actor: str,
    *,
    warm: bool = False,
    campaign_id: str | None = None,
) -> None:
    """Lock a person and company to one profile, with at most two active people."""
    try:
        with db.sessions.begin() as session:
            require_staff(session, actor, owner=True)
            person, profile = (
                session.get(Person, person_id),
                session.get(LinkedInProfile, profile_id),
            )
            if not person or not profile or profile.status != "active" or not person.linkedin_url:
                raise ValueError("Choose a verified person and active member profile")
            require_staff(session, profile.user_id)
            if person.presence in {"low", "none"}:
                raise ValueError("Low LinkedIn presence: keep this person on email or phone")
            coordination = lock_company(session, person.company_id)
            if campaign_id:
                from app.linkedin.campaigns import active_campaign

                rule = active_campaign(session, campaign_id)
                if (
                    not rule
                    or rule.value["profile_id"] != profile_id
                    or session.get(PersonOwnership, person_id)
                ):
                    return
            if reason := outreach_block(session, person.company_id):
                raise ValueError(reason)
            locks = list(
                session.scalars(
                    select(PersonOwnership)
                    .join(Person)
                    .where(Person.company_id == person.company_id)
                )
            )
            if locks and not any(
                lock.active or (lock.locked_until and lock.locked_until > utc_now())
                for lock in locks
            ):
                coordination.profile_id = None
            if coordination.profile_id and coordination.profile_id != profile_id:
                raise ValueError(
                    "This company is assigned to another member; owner release required"
                )
            current = session.get(PersonOwnership, person_id)
            if (
                current
                and current.profile_id != profile_id
                and (current.active or (current.locked_until and current.locked_until > utc_now()))
            ):
                raise ValueError("This person is locked to another member")
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(PersonOwnership)
                    .join(Person)
                    .where(
                        Person.company_id == person.company_id,
                        PersonOwnership.active.is_(True),
                        Person.id != person_id,
                    )
                )
                or 0
            )
            if count >= 2:
                raise ValueError("At most two people per company can have active sequences")
            coordination.profile_id = profile_id
            if not current:
                current = PersonOwnership(person_id=person_id, profile_id=profile_id)
                session.add(current)
            current.profile_id, current.active, current.warm = profile_id, True, warm
            current.released_at, current.locked_until = None, None
            current.reason = (
                f"LinkedIn campaign {campaign_id}"
                if campaign_id
                else "Existing first-degree connection confirmed"
                if warm
                else "Owner assignment"
            )
            emit(
                session,
                person.company_id,
                "ownership:" + person_id + ":" + str(coordination.revision),
                "linkedin_assigned",
                {"person_id": person_id, "profile_id": profile_id, "warm": warm},
                actor=actor,
            )
    except IntegrityError as error:
        raise ValueError("Concurrent assignment changed; reload before assigning") from error


def release_company(db: Database, company_id: str, actor: str, reason: str) -> None:
    """Explicit owner override releases routing without clearing stops or approvals."""
    if not 10 <= len(reason.strip()) <= 500:
        raise ValueError("Record why the company ownership should be released")
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        coordination = lock_company(session, company_id)
        coordination.profile_id = None
        for item in session.scalars(
            select(PersonOwnership).join(Person).where(Person.company_id == company_id)
        ):
            item.active, item.released_at, item.locked_until = False, utc_now(), utc_now()
            item.reason = reason.strip()
        from app.linkedin.coordination import cancel_pending

        cancel_pending(session, company_id, "Owner released LinkedIn routing", email=False)
        emit(
            session,
            company_id,
            "release:" + company_id + ":" + str(coordination.revision),
            "linkedin_released",
            {"reason": reason.strip()},
            actor=actor,
        )


def end_ownership(session: Session, company_id: str) -> None:
    """End active work while retaining the 180-day owner lock."""
    for item in session.scalars(
        select(PersonOwnership)
        .join(Person)
        .where(Person.company_id == company_id, PersonOwnership.active.is_(True))
    ):
        item.active = False
        item.locked_until = utc_now() + timedelta(days=180)
