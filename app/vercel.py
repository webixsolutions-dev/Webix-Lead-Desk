"""Explicit Vercel entry point; local commands continue using the app factory."""

from app.core.config import Settings
from app.main import create_app

# Never fall back to a function-local SQLite database, even if APP_MODE was copied
# from the PC example. Credentials and allowed hosts still come from the environment.
app = create_app(Settings(app_mode="VERCEL"))
