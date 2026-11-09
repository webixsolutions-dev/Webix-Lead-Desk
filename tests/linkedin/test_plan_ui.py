"""Member queue isolation and owner-only bounded planner controls."""

from fastapi.testclient import TestClient

from app.db.session import Database
from app.linkedin.planner import plan_day
from tests.conftest import Account, csrf_from, sign_in
from tests.linkedin.test_planner import NOW, seed


def test_planner_and_profile_limits_are_visible_only_to_authorized_staff(
    db: Database, owner: Account, reviewer: Account, client: TestClient
) -> None:
    """Owner sees the plan; another staff member cannot view its people or change quotas."""
    profile, _ = seed(db, owner)
    plan_day(db, NOW)
    sign_in(client, owner)
    page = client.get("/linkedin/plan")
    assert page.status_code == 200 and "Alex Example" in page.text
    assert "No LinkedIn password" not in page.text
    settings = client.get("/settings/linkedin")
    assert settings.status_code == 200 and "Daily limits" in settings.text
    csrf = csrf_from(settings.text)
    assert (
        client.post(
            f"/settings/linkedin/{profile}/quotas", data={"csrf_token": csrf, "invites": "21"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/settings/linkedin/{profile}/quotas", data={"csrf_token": csrf, "invites": "4"}
        ).status_code
        == 303
    )
    assert client.get("/").status_code == 200
    sign_in(client, reviewer)
    page = client.get("/linkedin/plan")
    assert page.status_code == 200 and "Alex Example" not in page.text
    assert (
        client.post(
            f"/settings/linkedin/{profile}/quotas",
            data={"csrf_token": csrf_from(page.text), "invites": "4"},
        ).status_code
        == 403
    )
    assert (
        client.post("/linkedin/plan", data={"csrf_token": csrf_from(page.text)}).status_code == 403
    )
