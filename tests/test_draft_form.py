"""Repeated observation selections remain distinct from security-sensitive form fields."""

from urllib.parse import urlencode

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.outreach_models import Draft
from app.db.research_models import Evidence
from app.db.session import Database
from tests.conftest import Account, csrf_from, sign_in
from tests.test_drafting import BODY
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def test_multiple_observations_without_duplicate_csrf(
    db: Database, client: TestClient, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """Multi-choice evidence survives parsing while duplicated security fields still fail."""
    with db.sessions.begin() as session:
        extra = Evidence(
            company_id=drafting_setup["company_id"],
            kind="visual",
            source_url="https://example.com/",
            facts={"note": "Synthetic visual check"},
        )
        session.add(extra)
        session.flush()
        extra_id = extra.id
    sign_in(client, owner)
    url = f"/leads/{drafting_setup['company_id']}/draft"
    csrf = csrf_from(client.get(url).text)
    pairs = [
        ("csrf_token", csrf),
        ("campaign_id", drafting_setup["campaign_id"]),
        ("contact_id", drafting_setup["contact_id"]),
        ("mailbox_id", drafting_setup["mailbox_id"]),
        ("subject", "A question about estimates"),
        ("body", BODY),
        ("evidence_ids", drafting_setup["evidence_id"]),
        ("evidence_ids", extra_id),
    ]
    assert (
        client.post(
            url,
            content=urlencode(pairs),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 303
    )
    with db.sessions() as session:
        item = session.scalar(select(Draft))
        assert item
        assert set(item.snapshot["evidence_ids"]) == {extra_id, drafting_setup["evidence_id"]}
    assert (
        client.post(
            url,
            content=urlencode(pairs + [("csrf_token", csrf)]),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 400
    )
