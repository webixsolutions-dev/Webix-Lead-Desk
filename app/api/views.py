"""Shared template rendering and read-only dashboard context."""

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.responses import HTMLResponse

from app.api.dependencies import Principal, services
from app.core.clock import utc_now
from app.core.preferences import get_preferences
from app.db.lead_models import Company
from app.db.models import Job, WorkerInstance
from app.ui.preview import LEADS, NAVIGATION

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "ui" / "templates"))


def render(request: Request, name: str, context: dict[str, Any], status: int = 200) -> HTMLResponse:
    """Render HTML with Jinja's automatic escaping for user-supplied content."""
    return TEMPLATES.TemplateResponse(
        request=request, name=name, context=context, status_code=status
    )


def dashboard_context(request: Request, principal: Principal, page: str) -> dict[str, Any]:
    """Combine real infrastructure status with clearly separated preview fixtures."""
    app = services(request)
    prefs, version = get_preferences(app.db, app.settings)
    now = utc_now()
    with app.db.sessions() as session:
        lead_count = session.scalar(select(func.count()).select_from(Company)) or 0
        worker = session.get(WorkerInstance, "primary")
        worker_online = worker is not None and worker.lease_expires_at > now
        queued = (
            session.scalar(select(func.count()).select_from(Job).where(Job.state == "queued")) or 0
        )
        completed = (
            session.scalar(select(func.count()).select_from(Job).where(Job.state == "succeeded"))
            or 0
        )
        latest_job = session.scalar(select(Job).order_by(Job.created_at.desc()).limit(1))
    return {
        "user": principal.user,
        "csrf": principal.csrf_token,
        "page": "settings" if page in {"learning", "partners", "operations"} else page,
        "navigation": [
            item for item in NAVIGATION if item[0] != "operations" or principal.user.role == "owner"
        ],
        "preferences": prefs,
        "settings_version": version,
        "mode": app.settings.app_mode,
        "worker_online": worker_online,
        "worker_seen": worker.heartbeat_at if worker is not None else None,
        "queued_jobs": queued,
        "completed_jobs": completed,
        "latest_job": latest_job,
        "local_time": now.astimezone(ZoneInfo(prefs.display_timezone)).strftime("%I:%M %p"),
        "calgary_time": now.astimezone(ZoneInfo("America/Edmonton")).strftime("%I:%M %p"),
        "today_date": now.astimezone(ZoneInfo(prefs.display_timezone)).strftime("%A, %d %B"),
        "leads": LEADS if prefs.preview_enabled else [],
        "lead_count": lead_count,
        "sample_count": len(LEADS) if prefs.preview_enabled else 0,
        "saved": False,
        "error": None,
    }
