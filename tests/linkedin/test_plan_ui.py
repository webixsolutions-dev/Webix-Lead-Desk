"""Member queue isolation and owner-only bounded planner controls."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.models import Job
from app.db.session import Database
from app.jobs.queue import acquire_worker
from app.linkedin.identity import add_profile
from app.linkedin.planner import plan_day
from tests.acquisition.helpers import business
from tests.conftest import Account, csrf_from, sign_in
from tests.linkedin.test_planner import NOW, seed


def test_linkedin_campaign_discovery_requires_its_own_worker_handler(
    db: Database, owner: Account, client: TestClient, settings: Settings
) -> None:
    """The industry page can activate routing and queue LinkedIn-only discovery."""
    _, campaign = business(db, owner)
    profile = add_profile(db, owner.user.id, "https://linkedin.com/in/search-worker", owner.user.id)
    sign_in(client, owner)
    page = client.get("/linkedin/campaigns")
    assert page.status_code == 200 and "LinkedIn campaigns" in page.text
    csrf = csrf_from(page.text)
    url = f"/linkedin/campaigns/{campaign.id}"
    assert (
        client.post(
            url,
            data={"csrf_token": csrf, "profile_id": profile.id, "active": "true", "version": "0"},
        ).status_code
        == 303
    )
    discovery = f"{url}/discover"
    unavailable = client.post(discovery, data={"csrf_token": csrf})
    assert unavailable.status_code == 422 and "Start worker v0.33.1" in unavailable.text
    assert acquire_worker(db, settings, "linkedin-worker", utc_now())
    assert client.post(discovery, data={"csrf_token": csrf}).status_code == 303
    assert "Business search queued" in client.get("/linkedin/campaigns").text
    with db.sessions() as session:
        job = session.scalar(select(Job).where(Job.kind == "linkedin_discover"))
        assert job and job.payload["routing_version"] == 1


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
    assert "data-settings-workspace" in settings.text
    unified = client.get("/settings")
    assert (
        'id="linkedin"' in unified.text
        and "https://www.linkedin.com/in/member-test" in unified.text
    )
    assert f"/settings/linkedin/{profile}/quotas" in unified.text
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
