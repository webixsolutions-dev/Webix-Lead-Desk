# Changelog

## Unreleased

- Fix Vercel FastAPI discovery with an explicit top-level application entry point, preserving the PC app factory.
- Require PostgreSQL and secure cookies in the Vercel profile; reject ephemeral SQLite even if a PC profile is copied.
- Document hosted configuration, explicit migrations/account setup and the separate PC worker.

## 0.1.0 — 2026-11-08

- Add local owner/reviewer sign-in, revocable sessions, CSRF protection and login throttles.
- Add portable Alembic migrations for identity, preferences, audit events and jobs.
- Add six responsive dashboard tabs with explicitly fictional previews.
- Apply the supplied Webix Solutions logo with transparency and red/black branding across the dashboard and sign-in screen.
- Add owner-only saved settings with optimistic version conflict protection.
- Add a separate durable health-job worker with single-worker leases and restart recovery.
- Add Windows/Linux setup scripts, offline tests, strict typing, lint and secret scanning.
- Keep all API and mailbox integrations disabled. No real lead data or live sending.

The owner approved publishing Phase 1 after the branding update. CI creates the initial `v0.1.0` tag only after the merged main commit passes all checks. Existing tags are never moved; later phases still require separate approval.

