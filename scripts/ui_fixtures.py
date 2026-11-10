"""Render synthetic authenticated pages for offline browser accessibility checks in CI."""

import json
import re
import secrets
import shutil
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.auth import create_user
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.preferences import Preferences, save_preferences
from app.db.migrate import migrate
from app.db.research_models import AICall, BudgetAccount
from app.db.session import create_database
from app.main import create_app
from app.playbook.bundles import install_templates
from scripts.ui_proposal import seed as seed_proposal
from scripts.ui_security import capture as capture_security
from scripts.ui_seed import seed_conversation_review, seed_review


def main() -> None:
    """Write disposable fixture HTML only; never load environment or production settings."""
    target = Path("screenshots/ui-fixtures")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree("app/ui/static", target / "static", dirs_exist_ok=True)
    with TemporaryDirectory() as folder:
        config = Settings(
            _env_file=None,
            database_url=f"sqlite:///{folder}/ui.sqlite",
            data_dir=Path(folder),
            session_secret=SecretStr(secrets.token_urlsafe(48)),
            encryption_key=SecretStr(Fernet.generate_key().decode()),
        )
        migrate(config)
        db = create_database(config)
        password = secrets.token_urlsafe(24)
        owner = create_user(db, "synthetic@example.test", "Demo Owner", password, "owner")
        editor_url, approval_url = seed_review(db, owner.id)
        with db.sessions.begin() as session:
            install_templates(session, owner.id)
        now = utc_now()
        with db.sessions.begin() as session:
            session.add(
                BudgetAccount(
                    key="primary",
                    allowance=170_000_000,
                    committed=13_400_000,
                    expires_at=now + timedelta(days=25),
                    verified_by=owner.id,
                )
            )
            for day in range(7):
                session.add(
                    AICall(
                        request_key=f"synthetic-{day}",
                        model="demo-model",
                        category="audits",
                        state="complete",
                        charged=(day + 1) * 300_000,
                        reserved=(day + 1) * 300_000,
                        created_at=now - timedelta(days=day),
                    )
                )
            session.add(
                AICall(
                    request_key="synthetic-pending",
                    model="demo-model",
                    category="enrichment",
                    state="uncertain",
                    charged=None,
                    reserved=5_000_000,
                    created_at=now,
                )
            )
        with TestClient(create_app(config), follow_redirects=False) as client:
            login = client.get("/login")
            token = re.search(r'name="csrf_token" value="([^"]+)"', login.text)
            assert token
            assert (
                client.post(
                    "/login",
                    data={"email": owner.email, "password": password, "csrf_token": token[1]},
                ).status_code
                == 303
            )
            routes = {
                "today": "/",
                "editor": editor_url,
                "leads": "/leads",
                "outbox": "/conversations/drafts",
                "pipeline": "/pipeline",
                "conversations": "/conversations",
                "campaigns": "/leads/campaigns",
                "bookings": "/pipeline/bookings",
                "settings": "/settings",
                "security": "/settings/security",
                "linkedin": "/linkedin/plan",
                "usage": "/settings/ai-usage",
                "message": "/leads/preview/prairie",
            }
            (target / "routes.json").write_text(
                json.dumps({"editor": editor_url, "approval": approval_url})
            )
            (target / "login.html").write_text(login.text, encoding="utf-8")
            for version, size in enumerate(("normal", "large")):
                save_preferences(
                    db,
                    Preferences(
                        workspace_name="Demo Lead Desk",
                        display_timezone="Asia/Karachi",
                        sending_timezone="America/Edmonton",
                        preview_enabled=True,
                        text_size=size,
                    ),
                    version,
                    owner.id,
                    utc_now(),
                )
                for name, path in routes.items():
                    result = client.get(path)
                    assert result.status_code == 200, (path, result.status_code)
                    (target / f"{name}-{size}.html").write_text(result.text, encoding="utf-8")
            reply_url = seed_conversation_review(db, owner.id, approval_url)
            proposal_routes = seed_proposal(db, owner.id)
            for version, size in enumerate(("normal", "large"), start=2):
                save_preferences(
                    db,
                    Preferences(
                        workspace_name="Demo Lead Desk",
                        display_timezone="Asia/Karachi",
                        sending_timezone="America/Edmonton",
                        preview_enabled=True,
                        text_size=size,
                    ),
                    version,
                    owner.id,
                    utc_now(),
                )
                result = client.get(reply_url)
                assert result.status_code == 200
                (target / f"reply-{size}.html").write_text(result.text, encoding="utf-8")
                for name, path in proposal_routes.items():
                    result = client.get(path)
                    assert result.status_code == 200
                    (target / f"{name}-{size}.html").write_text(result.text, encoding="utf-8")
                research = client.get(editor_url.split("/draft")[0])
                assert research.status_code == 200
                (target / f"research-{size}.html").write_text(research.text, encoding="utf-8")
            capture_security(client, db, config, owner.id, password, target)
        db.engine.dispose()


if __name__ == "__main__":
    main()
