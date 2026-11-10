"""Owner review forms and reviewer isolation through the actual HTTP routes."""

from fastapi.testclient import TestClient

from app.calls.access import assign_calls
from app.db.session import Database
from app.linkedin.identity import add_profile
from tests.acquisition.helpers import business
from tests.conftest import Account, csrf_from, sign_in
from tests.linkedin.test_drafts_actions import invitation


def test_owner_review_forms_show_exact_revision_and_require_csrf(
    client: TestClient, db: Database, owner: Account
) -> None:
    """The approval button carries an exact draft hash; a bad form cannot approve it."""
    task_id, _, draft = invitation(db, owner)
    sign_in(client, owner)
    page = client.get("/linkedin/tasks/" + task_id)
    assert page.status_code == 200 and draft.digest in page.text
    assert (
        client.post(
            f"/linkedin/tasks/{task_id}/approve",
            data={"draft_id": draft.id, "digest": draft.digest},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/linkedin/tasks/{task_id}/approve",
            data={"csrf_token": csrf_from(page.text), "draft_id": draft.id, "digest": draft.digest},
        ).status_code
        == 303
    )
    assert client.get("/linkedin/queue").status_code == 200
    assert client.get("/pipeline").status_code == 200
    assert "LinkedIn approvals" in client.get("/conversations/drafts").text


def test_reviewer_cannot_read_other_tasks_global_lists_or_calls(
    client: TestClient, db: Database, owner: Account, reviewer: Account
) -> None:
    """Direct URLs are protected as well as navigation and list filters."""
    task_id, _, _ = invitation(db, owner)
    company, campaign = business(db, owner)
    sign_in(client, reviewer)
    csrf = csrf_from(client.get("/").text)
    for url in ["/leads", "/outbox", "/pipeline", "/inbox", "/demos", "/settings"]:
        assert client.get(url).status_code == 403
    assert client.get("/linkedin/tasks/" + task_id).status_code == 404
    assert company.name not in client.get("/leads/calls").text
    assert (
        client.post(
            f"/calls/{company.id}/outcome",
            data={
                "csrf_token": csrf,
                "campaign_id": campaign.id,
                "request_key": "unauthorized",
                "outcome": "no_answer",
                "notes": "Should not be accepted",
            },
        ).status_code
        == 422
    )
    profile = add_profile(
        db, reviewer.user.id, "https://linkedin.com/in/reviewer-ui", owner.user.id
    )
    import pytest

    from app.linkedin.identity import release_company

    with pytest.raises(ValueError, match="Release"):
        assign_calls(db, company.id, profile.id, owner.user.id)
    release_company(
        db, company.id, owner.user.id, "Owner reassigns the synthetic company to the reviewer."
    )
    assign_calls(db, company.id, profile.id, owner.user.id)
    page = client.get("/leads/calls")
    assert page.status_code == 200 and company.name in page.text
    assert 'href="/leads/calls"' in page.text and 'href="/settings"' not in page.text
