# Architecture

The owner approved starting Phase 1 after reviewing the architecture plan. This repository documents implemented behavior and preserves boundaries for later approved phases.

## Runtime

- Python 3.12, FastAPI, server-rendered Jinja2, local CSS/JavaScript and vendored HTMX.
- SQLAlchemy 2 and Alembic, SQLite for the PC pilot and portable models for PostgreSQL.
- A separate worker polls a durable database queue. The HTTP process never starts a worker.
- The worker processes health checks and fixed-source Open Calgary discovery. Paid and mailbox integrations remain disabled.

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

This section records the initial foundation. Subsequent implementation sections below describe the completed modules and migrations; optional provider adapters remain explicitly unavailable.

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

Phase 2 adds campaigns/runs, companies/domains, campaign membership, source provenance, contacts/bases, global suppression and channel reservations. Website matches require recorded evidence; shared domains never silently merge companies. Later migrations add audits, drafting, delivery and pipeline tables.

## Job lifecycle

Enqueue stores a unique key. One worker obtains a singleton lease, claims a due queued job with a unique token and records an attempt. A completed job is never claimed again. A delayed retry retains the same logical job. Temporary failures back off with jitter and stop at the configured cap; permanent failures remain visible.

Expired health-job claims can be recovered because the handler is idempotent and has no external side effects. A stale worker cannot overwrite a newer result. This recovery rule must never be reused blindly for SMTP. Later uncertain send attempts must stay held for reconciliation rather than being replayed on lease expiry.

## Authentication and boundaries

Private accounts are created from the local CLI. No public registration exists. Passwords are hashed with Argon2id. Session cookies hold random bearer tokens whose hashes are stored in the database. Sessions expire and can be revoked. Cookies use HttpOnly and SameSite=Strict; secure deployments also require Secure.

Login has a signed CSRF cookie and account/source rate limits. Authenticated mutations require a session-bound CSRF token. Owner permissions are enforced server-side. Session checks re-read active user state. Forms have size/field limits, templates escape user text, pages prohibit caching, and browser security headers restrict scripts, framing and form destinations.

Only safe workspace preferences are editable in Phase 1. Unknown setting fields are rejected. Concurrent edits require matching versions. Errors and operational logs never contain credentials or full message bodies. Access logging is disabled in the provided start scripts.

## Operating modes

PC mode runs locally with SQLite, an explicit web process and a separate worker. The PC must remain awake for queued work to progress. VPS mode requires PostgreSQL and HTTPS; Phase 10 provides explicit transfer tools and service templates. Changing APP_MODE does not move data or deploy a server.

Vercel web hosting uses the explicit `app.vercel:app` entry point, which builds the same application through the existing factory and enforces PostgreSQL and secure cookies. Its database and exact allowed domains must be configured separately; see [deployment setup](deploy-to-vercel.md). The worker stays a separate PC/VPS process connected to that environment's database. Importing the entry point does not open a database connection or run migrations. The local PC dashboard remains bound to loopback.

## Phase boundaries

Phase 2 implements permitted imports and deduplication. The owner authorized sequential checked merges; see delivery-plan.md. Phase 3 adds factual audits and budgeted AI. Phase 4 adds immutable drafts, review and Playbook/offer/proof libraries. Phase 5 adds sender locks, approved delivery, inbox cancellation and basic call cards. Phase 6 is a separately approved small live pilot. Later phases cover extended pipeline/demos, partners, approved learning and VPS migration.

Fifty first touches per day is a later target constrained by sending windows, follow-up capacity and verified account limits. No delivery or sales outcome is guaranteed by the foundation.



## Phase 3 research implementation

Research adds `website_audits`, `evidence`, `assessments`, `ai_budget_accounts` and
`ai_calls`. Audit jobs are safe to recover because the stored result is unique per job.
Model jobs are not automatically retried after lease expiry; their spending reservation
is retained. A renewal thread extends the singleton worker and current job lease during
bounded network requests. Database finalization still checks the claim token.

`app/integrations/public_http.py` is the sole arbitrary-website transport. Fixed City
of Calgary and OpenAI endpoints use separate mockable provider wrappers. The application
never uses website text as agent instructions. See `docs/research.md` for the full bounds.


## Phase 4 review implementation

`mailboxes` hold disabled sender identity profiles. `conversations` bind one business to its
contact/campaign/sender. `drafts` store immutable canonical JSON snapshots with per-step revision
numbers and SHA-256 digests. `draft_approvals` bind the owner's decision to one exact digest.
An edit conditionally supersedes its predecessor and creates a new unapproved revision. No
content is patched into an approved row. Contact-basis and suppression checks run at approval
and will run again immediately before delivery. Sender profiles are never provider credentials.

`playbook_versions` are append-only, with unique scope/version and optimistic publish checks.
`offers` store owner-provided CAD cents; `proof_cards` require explicit factual verification.
Optional AI drafts reserve spend before one no-retry call and cannot approve themselves. The
copyable brief path works with the owner's own ChatGPT session without API use or browser login.


## Phase 5 delivery implementation

`delivery_models.py` adds encrypted integration secrets, sender readiness, local-day counters,
IMAP checkpoints, one send intent per conversation step, transport attempts and deduplicated replies.
Queue claims now include the singleton worker owner. The worker plans short inbox/send jobs;
Vercel handles authenticated reviews and scheduling only. No in-process background tasks run there.
The dedicated encryption key is environment-held and must follow the database during migration.

The last pre-DATA check revalidates approval, reply/suppression state, mailbox readiness and both
leases. SMTP cannot atomically commit with our database: a tiny interval remains between checking
and the remote server accepting DATA. Uncertain results are therefore held, never replayed.
Accepted late responses can record known delivery without reviving a stopped conversation.
Raw inbox content is untrusted data and cannot call tools or approve/resume outreach.


## Phase 6 pilot gate

`app/pilot/` owns source-review membership, launch/pause/acceptance state and real-data readiness.
A singleton `Pilot` serializes the five-place first-touch cap; `PilotMember` freezes the reviewed
4/4/2 cohort and `PilotReservation` prevents retries from spending another place. Dispatch checks
the gate both before reservation and immediately before DATA. After accepted pilot completion,
normal exact-message approvals and all Phase 5 delivery limits still apply.
The worker lease records its mode and live-send flag for honest reporting; it never records keys.
Vercel rejects a dedicated worker process and remains web-only.

## Phase 7 pipeline

`opportunities` and `pipeline_events` record human decisions separately from reply labels.
`demo_versions` bind immutable owner briefs to evidence and separately approved share links.
Exact draft snapshots include the approved concept digest and URL. Sender dispatch rechecks
that binding immediately before DATA; acceptance alone advances the Demo sent stage.

## Phase 8 partners and reports

`partner_reviews` records attributable country reviews; `partner_days` and `partner_slots`
serialize five-new-business capacity across campaigns before optional AI costs. Campaign
versions preserve historical import snapshots. `form_drafts` keeps immutable manual copy
and recorded outcomes; channel reservations are shared with SMTP. Reports aggregate
accepted events independently to prevent join multiplication. No external form submission runs.

## Phase 9 reviewed memory

`learning_proposals` captures attributable internal edit/rejection/outcome evidence. Raw websites
and incoming mail have no publication path. Applying a proposal and appending a Playbook version
share one transaction and expected-version check. Restoring guidance appends a new version;
old draft content, approvals and guidance references remain unchanged. No model training or
paid generation is involved in this memory workflow.

## Phase 10 operations

`runtime_control` provides a shared maintenance gate. `app/operations/` fences work, takes a consistent portable snapshot, verifies credential recovery and restores only empty current-schema databases in a transaction. A separate backup key protects files; environment secrets never enter the payload. Restored login sessions are omitted, all delivery identities and approval hashes remain intact, and maintenance stays on. The operator must stop web/workers and confirm the offline window; maintenance alone cannot cancel an in-flight write. See operations.md and move-to-vps.md.

## Outreach review corrections

Mailbox provider is selected explicitly and bound into new approval snapshots. Fixed provider hosts prevent arbitrary SMTP/IMAP routing. DNS routing is checked at the worker transport boundary before SMTP reservation, and is mockable.

Follow-up steps 1–4 are sequential, separately approved and spaced at least three days from previous acceptance. Concept responses are identified by their reviewed attachment binding rather than by numeric step; existing concept IDs remain compatible. Human replies stop all pending outreach; delayed/success DSNs are recorded without becoming prospect replies.

Optional audit Batch API adds nullable `ai_calls.batch` metadata in migration 0011. Reserve
budget, persist submission intent, submit once, persist the provider ID, then release the
worker job until the next poll. Ambiguous creates remain held; polling does not consume
transient-error retries. Drafting always remains synchronous. Both tasks validate output and
never approve messages.

Migration 0012 adds per-mailbox connection JSON, campaign follow-up offsets, automatic contact
routing evidence, explicit Draft.kind/sequence_step and owner-recorded local team members.
`Draft.step` remains the unique monotonic delivery slot for compatibility with SendIntent.
A follow-up's sequence position is separate: concepts/replies cannot occupy or replace it.
Historical snapshots/digests remain unchanged; new snapshots bind kind and sequence position.

Six primary navigation items expose partner/client filters and Results under Leads, with
Learning and owner-only Operations sections in Settings. The partner Playbook is seeded once
and versioned like owner guidance. Local meetings use owner data, never website inferences.

Hosting remains unchanged: Vercel handles bounded web requests, PostgreSQL is shared, and one
PC/VPS worker handles SMTP, IMAP and queued audits. No Neon resource is provisioned by v0.11.


## Acquisition workflow (v0.12)

Sections 11–15 add `sources/enrichment/`, `scoring/`, `calls/` and `journey/`.
Migrations 0013/0014 introduce search reservations/quota buckets, score policies and
history, human calls/note suggestions and append-only lead events. Existing
WorkspaceSetting/AuditEvent tables version search configuration; encrypted
IntegrationSecret stores optional Places keys. See
[ADR 0013](decisions/0013-acquisition-workflow.md) and [setup](acquisition.md).
The single leased PC/VPS worker handles discovery and its continuation; Vercel's
web entrypoint does not create a persistent background loop.


LinkedIn L1 introduces verified professional identities and one serialized company
coordination record shared by manual LinkedIn, email, calls and forms. See
[ADR 0014](decisions/0014-linkedin-channel.md) for the L1–L5 build boundary and
[LinkedIn setup](linkedin.md) for review and stop semantics.

LinkedIn L2 uses database-backed profile locks, quota rows, sequence progress and human
leases. The dedicated worker plans bounded weekday batches without executing them.
Named-person day reservations fence both SMTP and future human LinkedIn starts. See
[LinkedIn setup](linkedin.md) for limits, catch-up, Windows startup and recovery.


### LinkedIn L3

Separate immutable draft/approval tables preserve the email schema. `app/linkedin/`
contains exact-copy checks, member action leases, human sequence progression, voice
review, reply cards and optional copy suggestions. API routes use both role and
assignment checks. The same monthly AI ledger now covers every provider entry point;
unknown charges remain reserved, and worker priorities process replies before research.
All planned invitations and DMs remain manual; the worker never executes them.

L4 stores public post slots/revisions/approvals separately from person-targeted tasks.
The same member policy row serializes both. `app/linkedin/posting.py` schedules
short worker jobs; no work runs after a Vercel request finishes. Media uses private
1 MB base64 database chunks shared by PC/VPS/Vercel, retaining portable JSON backups.
OAuth state is excluded from backups; encrypted tokens use the existing credential
vault and key fingerprint. Maintenance turns in-flight posts into checked holds.

L5 adds immutable profile/post observation tables and a mutable owner risk-review
baseline. Recorded touch cohorts drive scoped weekly reports; task quotas are not
performance data. Automatic profile holds run in the worker and action-start fences.
Fortnight summaries enter the existing pending LearningProposal workflow and cannot
publish guidance. Publication-confirmation timestamps are nullable for legacy posts;
no historical timestamps or metrics are invented.


## v0.18 hosted operations

SQLAlchemy remains the only application database interface; Supabase Auth/REST keys
are not used. app/db/connection.py retries socket establishment only. Vercel uses
NullPool; PC/VPS PostgreSQL engines use 3 connections, no overflow, pre-ping and
300-second recycling. The worker restarts polling after transient DB disconnection
without replaying a previous job handler. Migration 0020 adds backup_status and RLS.
app/operations/hosted.py caches catalog checks for 60 seconds and exposes owner-only
status. scheduled_backup.py runs once a week from the dedicated worker loop; it
shares the encrypted exporter with CLI and recovery_snapshot.py fences the backup
copy for safe restore. No live workspace maintenance toggling is needed for backups.
SQLite/PC remains supported; hosted processes share one URL and credential key.

## v0.19 Windows installer

`app/desktop/` adds a Tkinter UI, DPAPI configuration store, Windows mutex/startup
adapter and threaded controller. It uses the existing `app/jobs/runner.py` with a
cooperative stop Event and a started callback after acquiring the database lease.
Network work stays off Tk's UI thread. Stop never kills the job thread. The GUI
is a worker companion; the Vercel dashboard and PostgreSQL schema are unchanged.
No additional database tables or migrations. PC/VPS CLI remains supported.

`packaging/windows/` contains a PyInstaller one-folder spec and Inno Setup installer.
Python, native libraries, migrations, timezone data and logo are bundled in Windows
CI. Settings and backups live separately under the Windows user's AppData. Only
DPAPI-encrypted bytes are written atomically; existing keys survive updates. The
installer is per-user, creates shortcuts and respects the application's mutex.
Sign-in autostart is opt-in and removed on uninstall. Releases are published only
after lint/types/tests/secret scan, PostgreSQL and Windows packaging checks pass.
