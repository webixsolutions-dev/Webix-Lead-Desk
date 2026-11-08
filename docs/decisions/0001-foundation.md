# ADR 0001 — Local modular application and durable worker

Status: implemented for Phase 1.

The first release must run on Windows without API or mailbox credentials and remain portable to a future VPS. It must have one coding style, clear approval boundaries and inexpensive tests.

Use FastAPI/Jinja2 with a separate Python worker and a SQLAlchemy-backed durable queue. Use SQLite for the PC pilot and Alembic migrations that also run on PostgreSQL. Do not introduce Redis, a second frontend application or a managed paid job service in this phase.

Consequences: the PC must be on for jobs to run. The web app remains available when its worker is stopped, and displays that state. One live worker lease prevents accidental duplicate processing. Queue recovery is currently limited to harmless health checks; future SMTP must use a stricter uncertainty policy. Migration requires a controlled cutover, not a settings toggle.

The owner authorized a temporarily public repository after initially requesting private storage. That exception applies to source visibility only. Secrets and real operational data remain excluded.

