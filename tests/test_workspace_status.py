"""The simplified dashboard must not misrepresent an offline sender's switch."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.clock import utc_now
from app.db.models import WorkerInstance
from app.db.session import Database
from tests.conftest import Account, sign_in


@pytest.mark.parametrize(
    ("live", "reported", "expected"),
    [
        (True, True, "On · approval required"),
        (True, False, "Off"),
        (False, True, "Not reported"),
        (False, False, "Not reported"),
    ],
)
def test_delivery_status_requires_current_worker(
    client: TestClient, owner: Account, db: Database, live: bool, reported: bool, expected: str
) -> None:
    """A stale lease never produces a current on/off claim on the home page."""
    now = utc_now()
    with db.sessions.begin() as session:
        session.add(
            WorkerInstance(
                key="primary",
                owner_id="synthetic",
                heartbeat_at=now,
                lease_expires_at=now + timedelta(minutes=5 if live else -5),
                runtime_config={"app_mode": "PC", "live_send_enabled": reported},
            )
        )
    sign_in(client, owner)
    page = client.get("/")
    assert page.status_code == 200
    assert f"<span>Email sending</span><strong>{expected}</strong>" in page.text
