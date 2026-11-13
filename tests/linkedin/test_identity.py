"""Identity aliases, ownership locks and company-wide routing limits."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.linkedin_models import Person, PersonOwnership
from app.db.session import Database
from app.linkedin.identity import (
    add_profile,
    assign_person,
    confirm_person,
    linkedin_url,
    release_company,
)
from tests.acquisition.helpers import business
from tests.conftest import Account, csrf_from, sign_in


def test_registration_lists_only_unregistered_staff(
    db: Database, client: TestClient, owner: Account, reviewer: Account
) -> None:
    """The next profile must belong to a different real staff account."""
    add_profile(db, owner.user.id, "https://linkedin.com/in/synthetic-owner", owner.user.id)
    sign_in(client, owner)
    page = client.get("/settings/linkedin")
    assert f'<option value="{owner.user.id}"' not in page.text
    assert f'<option value="{reviewer.user.id}"' in page.text
    form = {
        "csrf_token": csrf_from(page.text),
        "user_id": owner.user.id,
        "profile_url": "https://linkedin.com/in/synthetic-staff",
        "confirmed": "true",
    }
    result = client.post("/settings/linkedin/profile", data=form)
    assert result.status_code == 422 and "data-form-error" in result.text
    result = client.post("/settings/linkedin/profile", data={**form, "user_id": reviewer.user.id})
    assert result.status_code == 303
    assert (
        "All active staff accounts already have a LinkedIn profile"
        in client.get("/settings/linkedin").text
    )


def person(
    db: Database,
    company_id: str,
    owner: Account,
    name: str = "Alex Example",
    *,
    url: str = "https://www.linkedin.com/in/alex-example",
    email: str = "",
) -> Person:
    """Create an attributable fictional business contact, never a real prospect."""
    return confirm_person(
        db,
        company_id,
        owner.user.id,
        name=name,
        role="Owner",
        url=url,
        email=email,
        source_url="https://example.com/team",
        note="Checked the official company team listing",
        presence="active",
        decision_maker=True,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://linkedin.com.evil.test/in/alex",
        "https://linkedin.com/company/test",
        "http://linkedin.com/in/alex",
        "https://user@linkedin.com/in/alex",
        "https://linkedin.com/in/../feed",
        "javascript:alert(1)",
    ],
)
def test_profile_links_reject_nonmember_or_unsafe_urls(url: str) -> None:
    """Stored open-profile links cannot carry executable schemes or lookalike domains."""
    with pytest.raises(ValueError):
        linkedin_url(url)


def test_aliases_deduplicate_and_profile_member_is_unique(db: Database, owner: Account) -> None:
    """Tracking parameters, case and alternate identities cannot create a second person."""
    company, _ = business(db, owner)
    original = person(db, company.id, owner, email="alex@example.com")
    duplicate = person(
        db,
        company.id,
        owner,
        name="ALEX  EXAMPLE",
        url="https://ca.linkedin.com/in/ALEX-example/?trk=feed",
        email="alex@example.com",
    )
    assert duplicate.id == original.id
    with pytest.raises(ValueError, match="differs"):
        person(db, company.id, owner, email="different@example.com")
    add_profile(db, owner.user.id, "https://www.linkedin.com/in/synthetic-member", owner.user.id)
    with pytest.raises(ValueError, match="already registered"):
        add_profile(db, owner.user.id, "https://www.linkedin.com/in/another-member", owner.user.id)


def test_company_routing_cap_and_owner_release(
    db: Database, owner: Account, reviewer: Account
) -> None:
    """Two contacts share one member; a different member requires explicit owner release."""
    company, _ = business(db, owner)
    first = add_profile(db, owner.user.id, "https://linkedin.com/in/first-member", owner.user.id)
    second = add_profile(
        db, reviewer.user.id, "https://linkedin.com/in/second-member", owner.user.id
    )
    one = person(db, company.id, owner)
    two = person(
        db, company.id, owner, "Riley Example", url="https://linkedin.com/in/riley-example"
    )
    three = person(db, company.id, owner, "Sam Example", url="https://linkedin.com/in/sam-example")
    assign_person(db, one.id, first.id, owner.user.id, warm=True)
    with pytest.raises(ValueError, match="another member"):
        assign_person(db, one.id, second.id, owner.user.id)
    with pytest.raises(ValueError, match="another member"):
        assign_person(db, two.id, second.id, owner.user.id)
    assign_person(db, two.id, first.id, owner.user.id)
    with pytest.raises(ValueError, match="At most two"):
        assign_person(db, three.id, first.id, owner.user.id)
    release_company(
        db, company.id, owner.user.id, "Owner moved this company to the available member"
    )
    assign_person(db, one.id, second.id, owner.user.id)
    with db.sessions() as session:
        active = list(
            session.scalars(select(PersonOwnership).where(PersonOwnership.active.is_(True)))
        )
        assert len(active) == 1 and active[0].profile_id == second.id


def test_low_presence_and_generic_inbox_do_not_create_cold_invites(
    db: Database, owner: Account
) -> None:
    """A generic mailbox is not a person; stale LinkedIn presence keeps other channels."""
    company, _ = business(db, owner)
    with pytest.raises(ValueError, match="generic"):
        person(db, company.id, owner, email="info@example.com")
    member = add_profile(db, owner.user.id, "https://linkedin.com/in/member-test", owner.user.id)
    target = person(db, company.id, owner)
    with db.sessions.begin() as session:
        current = session.get(Person, target.id)
        assert current
        current.presence = "low"
    with pytest.raises(ValueError, match="Low LinkedIn"):
        assign_person(db, target.id, member.id, owner.user.id)


def test_concurrent_members_cannot_win_the_same_person(
    db: Database, owner: Account, reviewer: Account
) -> None:
    """Independent transactions serialize ownership instead of both accepting assignment."""
    company, _ = business(db, owner)
    target = person(db, company.id, owner)
    first = add_profile(db, owner.user.id, "https://linkedin.com/in/member-one", owner.user.id)
    second = add_profile(db, reviewer.user.id, "https://linkedin.com/in/member-two", owner.user.id)

    def attempt(profile: str) -> bool:
        """Exercise separate real database sessions with a simultaneous competing owner."""
        try:
            assign_person(db, target.id, profile, owner.user.id)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [first.id, second.id])) == [False, True]
