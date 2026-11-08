# Architecture

The owner approved starting Phase 1 after reviewing the architecture plan. This repository documents implemented behavior and preserves boundaries for later approved phases.

## Runtime

- Python 3.12, FastAPI, server-rendered Jinja2, local CSS/JavaScript and vendored HTMX.
- SQLAlchemy 2 and Alembic, SQLite for the PC pilot and portable models for PostgreSQL.
- A separate worker polls a durable database queue. The HTTP process never starts a worker.
- The app currently processes only a harmless health job. All external integrations fail closed.

Routes use domain services and typed configuration. Services use short-lived database sessions. Future HTTP, OpenAI, SMTP and IMAP adapters belong in `app/integrations/` and must be independently mockable. Future website content is data, never trusted instructions.

## Modules

| Path | Responsibility |
|---|---|
| app/api | Authentication, thin routes and template context |
| app/ui | Six tab templates, synthetic examples and local static assets |
| app/core | Configuration, preferences, password/session security and logging |
| app/db | Models, engine/session factory and Alembic migrations |
| app/jobs | Job claims, worker leases, attempts, retry bounds and recovery |
| app/integrations, app/policy, app/storage | Reserved external-adapter, approval-policy and private-artifact boundaries |
| app/sources, app/audit, app/drafting | Reserved discovery, factual audit and draft-generation boundaries |
| app/sending, app/inbox | Reserved transport, threading, inbox and suppression boundaries |
| app/pipeline, app/playbook | Reserved sales handoff and approved-learning boundaries |

The reserved modules contain no fake working implementation. Their schema and code are added only in the relevant approved phase.

## Phase 1 database

| Table | Purpose |
|---|---|
| users | Owner/reviewer accounts and Argon2id password hashes |
| sessions | Hashed bearer tokens, user, CSRF token and expiry |
| login_throttles | Atomic hashed account/source login buckets |
| settings | Non-secret preferences and optimistic version number |
| audit_events | Redacted action/actor/entity metadata |
| worker_instances | Singleton worker identity, heartbeat and lease |
| jobs | Unique job key, due time, state, claim token and bounded attempts |
| job_attempts | Historical claims and safe outcome codes |

Identifiers are UUID strings, timestamps are UTC-aware in application code, foreign keys are enabled on SQLite and schema changes go through Alembic. Preview businesses are static fixtures with reserved example domains, not database contacts.

Future tables will add campaigns, runs, companies/domains, contacts/bases, evidence/audits, offers/proofs, immutable drafts/approvals, conversations/sequences, mailboxes, send intents, inbound messages, suppression, pipeline/calls/demos, Playbook versions/proposals and AI cost reservations. They are not migrated in Phase 1.

## Job lifecycle

Enqueue stores a unique key. One worker obtains a singleton lease, claims a due queued job with a unique token and records an attempt. A completed job is never claimed again. A delayed retry retains the same logical job. Temporary failures back off with jitter and stop at the configured cap; permanent failures remain visible.

Expired health-job claims can be recovered because the handler is idempotent and has no external side effects. A stale worker cannot overwrite a newer result. This recovery rule must never be reused blindly for SMTP. Later uncertain send attempts must stay held for reconciliation rather than being replayed on lease expiry.

## Authentication and boundaries

Private accounts are created from the local CLI. No public registration exists. Passwords are hashed with Argon2id. Session cookies hold random bearer tokens whose hashes are stored in the database. Sessions expire and can be revoked. Cookies use HttpOnly and SameSite=Strict; secure deployments also require Secure.

Login has a signed CSRF cookie and account/source rate limits. Authenticated mutations require a session-bound CSRF token. Owner permissions are enforced server-side. Session checks re-read active user state. Forms have size/field limits, templates escape user text, pages prohibit caching, and browser security headers restrict scripts, framing and form destinations.

Only safe workspace preferences are editable in Phase 1. Unknown setting fields are rejected. Concurrent edits require matching versions. Errors and operational logs never contain credentials or full message bodies. Access logging is disabled in the provided start scripts.

## Operating modes

PC mode runs locally with SQLite, an explicit web process and a separate worker. The PC must remain awake for queued work to progress. VPS mode requires PostgreSQL and HTTPS and will be completed through an approved migration phase. Changing APP_MODE does not move data or deploy a server.

Vercel can be reconsidered as a later web-interface host, with persistent data and a separately designed worker. It is not used by this foundation. The local dashboard is not remotely accessible merely because its CSS supports phones.

## Phase boundaries

Phase 2 adds permitted imports and deduplication. Phase 3 adds factual audits and budgeted AI. Phase 4 adds immutable drafts, review and Playbook/offer/proof libraries. Phase 5 adds sender locks, approved delivery, inbox cancellation and basic call cards. Phase 6 is a separately approved small live pilot. Later phases cover extended pipeline/demos, partners, approved learning and VPS migration.

Fifty first touches per day is a later target constrained by sending windows, follow-up capacity and verified account limits. No delivery or sales outcome is guaranteed by the foundation.

