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

from app.api import auth, dashboard, delivery, drafts, playbook, research, sources
from app.core.config import Settings
from app.core.context import Services
from app.core.logging import configure_logging
from app.db.migrate import migration_head
from app.db.session import create_database


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
        response = await call_next(request)
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
        if config.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        """Expose only process liveness without account or connection details."""
        return {"status": "ok", "phase": "1"}

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
    app.include_router(dashboard.router)
    app.include_router(sources.router)
    app.include_router(research.router)
    app.include_router(drafts.router)
    app.include_router(playbook.router)
    app.include_router(delivery.router)
    app.mount(
        "/static", StaticFiles(directory=Path(__file__).parent / "ui" / "static"), name="static"
    )
    return app
