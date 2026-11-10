"""Persistent settings, roles and honest separation of preview content."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditEvent, Job, WorkspaceSetting
from app.db.session import Database
from tests.conftest import Account, csrf_from, sign_in


def test_six_pages_and_preview_detail_render(client: TestClient, owner: Account) -> None:
    """All six intended surfaces render with explicit fictional-data labeling."""
    sign_in(client, owner)
    for path in [
        "/",
        "/leads",
        "/conversations/drafts",
        "/pipeline",
        "/pipeline/demos",
        "/settings",
        "/leads/preview/prairie",
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert "Preview" in response.text
        assert "Preview examples are fictional" in response.text
    assert client.get("/leads/preview/unknown").status_code == 404


def test_settings_persist_and_hide_preview(
    client: TestClient, owner: Account, db: Database
) -> None:
    """An owner's real settings edit persists and removes synthetic leads."""
    sign_in(client, owner)
    response = client.post(
        "/settings",
        data={
            "csrf_token": csrf_from(client.get("/settings").text),
            "version": "0",
            "workspace_name": "My workspace",
            "display_timezone": "Asia/Karachi",
            "sending_timezone": "America/Edmonton",
        },
    )
    assert response.status_code == 303
    assert "My workspace" in client.get("/settings").text
    assert "Prairie Peak" not in client.get("/leads").text
    assert client.get("/leads/preview/prairie").status_code == 404
    with db.sessions() as session:
        stored = session.get(WorkspaceSetting, "workspace")
        assert stored is not None and stored.version == 1
        assert stored.value["preview_enabled"] is False
        event = session.scalar(select(AuditEvent).where(AuditEvent.action == "settings.updated"))
        assert event is not None and "workspace_name" in event.details["fields"]
        assert "My workspace" not in str(event.details)


def test_settings_conflict_and_unsupported_fields(client: TestClient, owner: Account) -> None:
    """A stale page cannot overwrite changes or enable live services."""
    sign_in(client, owner)
    values = {
        "csrf_token": csrf_from(client.get("/settings").text),
        "version": "0",
        "workspace_name": "First edit",
        "display_timezone": "Asia/Karachi",
        "sending_timezone": "America/Edmonton",
        "preview_enabled": "true",
    }
    assert client.post("/settings", data=values).status_code == 303
    assert client.post("/settings", data=values).status_code == 409
    values.update(version="1", live_send_enabled="true")
    assert client.post("/settings", data=values).status_code == 422
    assert client.post("/settings", data={"csrf_token": "invalid"}).status_code == 403


def test_reviewer_cannot_mutate_owner_state(
    client: TestClient, reviewer: Account, db: Database
) -> None:
    """Server-side roles protect settings and job submission, not just disabled buttons."""
    sign_in(client, reviewer)
    assert client.get("/settings").status_code == 403
    for path in ["/settings", "/jobs/health"]:
        assert (
            client.post(path, data={"csrf_token": csrf_from(client.get("/").text)}).status_code
            == 403
        )
    with db.sessions() as session:
        assert session.scalar(select(Job)) is None


def test_health_click_is_deduplicated(client: TestClient, owner: Account, db: Database) -> None:
    """Double-clicking the local health action creates one durable task."""
    sign_in(client, owner)
    token = csrf_from(client.get("/conversations/drafts").text)
    for _ in range(2):
        assert client.post("/jobs/health", data={"csrf_token": token}).status_code == 303
    with db.sessions() as session:
        jobs = session.scalars(select(Job)).all()
        assert len(jobs) == 1 and jobs[0].kind == "health"
    assert client.post("/send", data={"to": "prospect@example.test"}).status_code == 404


def test_settings_text_is_escaped(client: TestClient, owner: Account) -> None:
    """Saved user content cannot inject executable markup into the dashboard."""
    sign_in(client, owner)
    response = client.post(
        "/settings",
        data={
            "csrf_token": csrf_from(client.get("/settings").text),
            "version": "0",
            "workspace_name": "<script>alert(1)</script>",
            "display_timezone": "Asia/Karachi",
            "sending_timezone": "America/Edmonton",
            "preview_enabled": "true",
        },
    )
    assert response.status_code == 303
    page = client.get("/")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_worker_polling_requires_a_live_session(client: TestClient, owner: Account) -> None:
    """A partial refresh cannot expose job state after a session is lost."""
    headers = {"HX-Request": "true"}
    response = client.get("/status/worker", headers=headers)
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"
    sign_in(client, owner)
    response = client.get("/status/worker", headers=headers)
    assert response.status_code == 200
    assert "<html" not in response.text
    client.cookies.clear()
    assert client.get("/status/worker", headers=headers).status_code == 401
