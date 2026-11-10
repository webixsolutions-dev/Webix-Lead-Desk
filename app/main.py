"""FastAPI application factory with no embedded background workers."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse, Response

from app.api import (
    account_security,
    acquisition,
    approval_policy,
    auth,
    bookings,
    calls,
    command,
    dashboard,
    delivery,
    demos,
    drafts,
    forms,
    learning,
    linkedin,
    linkedin_activity,
    linkedin_media,
    linkedin_metrics,
    linkedin_planner,
    linkedin_posts,
    linkedin_review,
    linkedin_tasks,
    mailbox_tests,
    operations,
    partners,
    pilot,
    pipeline,
    playbook,
    proposals,
    research,
    signatures,
    sources,
)
from app.core.config import Settings
from app.core.context import Services
from app.core.logging import configure_logging
from app.db.migrate import migration_head
from app.db.session import create_database
from app.operations.control import maintenance_enabled


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the web process; require explicit Alembic migration before startup."""
    config = settings or Settings()
    configure_logging()
    db = create_database(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Verify migration state and dispose of connections on shutdown."""
        try:
            with db.engine.connect() as connection:
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
                if revision != migration_head():
                    raise RuntimeError("Run lead-desk migrate before starting the application")
        except SQLAlchemyError:
            db.engine.dispose()
            raise RuntimeError(
                "Database unavailable or unmigrated. Run lead-desk migrate."
            ) from None
        from app.core.clock import utc_now

        app.state.services.monitor.refresh(db, config, utc_now())
        try:
            yield
        finally:
            db.engine.dispose()

    app = FastAPI(
        title="Webix Lead Desk", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.services = Services(settings=config, db=db)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Prevent caching of private pages and set restrictive browser policies."""
        blocked = False
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path not in {
            "/login",
            "/login/verify",
            "/logout",
        }:
            with db.sessions() as session:
                blocked = maintenance_enabled(session)
        response = (
            JSONResponse(
                {"detail": "Maintenance is enabled; reviews are temporarily read-only."},
                status_code=503,
            )
            if blocked
            else await call_next(request)
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'"
        )
        if getattr(request.state, "email_preview", False):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = signatures.PREVIEW_CSP
        if config.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        """Expose only process liveness without account or connection details."""
        return {"status": "ok", "phase": "10"}

    @app.get("/health/ready")
    def readiness() -> JSONResponse:
        """Check database readiness without leaking connection errors."""
        try:
            with db.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    app.include_router(auth.router)
    app.include_router(account_security.router)
    app.include_router(approval_policy.router)
    app.include_router(command.router)
    app.include_router(proposals.router)
    app.include_router(bookings.router)
    app.include_router(acquisition.router)
    app.include_router(calls.router)
    app.include_router(linkedin.router)
    app.include_router(linkedin_activity.router)
    app.include_router(linkedin_planner.router)
    app.include_router(linkedin_tasks.router)
    app.include_router(linkedin_posts.router)
    app.include_router(linkedin_media.router)
    app.include_router(linkedin_metrics.router)
    app.include_router(linkedin_review.router)
    app.include_router(dashboard.router)
    app.include_router(sources.router)
    app.include_router(research.router)
    app.include_router(drafts.router)
    app.include_router(playbook.router)
    app.include_router(delivery.router)
    app.include_router(mailbox_tests.router)
    app.include_router(signatures.router)
    app.include_router(pilot.router)
    app.include_router(pipeline.router)
    app.include_router(demos.router)
    app.include_router(partners.router)
    app.include_router(forms.router)
    app.include_router(learning.router)
    app.include_router(operations.router)
    app.mount(
        "/static", StaticFiles(directory=Path(__file__).parent / "ui" / "static"), name="static"
    )
    return app
