"""Owner-only operating status with no environment values, mail content or backup downloads."""

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from starlette.responses import HTMLResponse

from app.api.dependencies import Owner, services
from app.api.views import dashboard_context, render
from app.db.delivery_models import SendIntent
from app.db.migrate import migration_head
from app.db.models import Job, WorkerInstance
from app.db.operations_models import RuntimeControl

router = APIRouter()


@router.get("/operations", response_class=HTMLResponse)
def operations_page(request: Request, principal: Owner) -> HTMLResponse:
    """Show the actual deployment profile and pending recovery work without claiming a live test."""
    app = services(request)
    context = dashboard_context(request, principal, "operations")
    with app.db.sessions() as session:
        context.update(
            control=session.get(RuntimeControl, "primary"),
            worker=session.get(WorkerInstance, "primary"),
            failed_jobs=session.scalar(
                select(func.count()).select_from(Job).where(Job.state == "failed")
            )
            or 0,
            uncertain_sends=session.scalar(
                select(func.count()).select_from(SendIntent).where(SendIntent.state == "uncertain")
            )
            or 0,
            database_kind=app.db.engine.dialect.name,
            schema=migration_head(),
            backup_key_ready=bool(app.settings.backup_key),
            credential_key_ready=bool(app.settings.encryption_key),
            process_delivery=app.settings.live_send_enabled and app.settings.app_mode != "VERCEL",
        )
    return render(request, "operations.html", context)
