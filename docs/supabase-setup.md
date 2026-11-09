# Hosted setup: Supabase Free + Vercel + Windows worker

Owner: Haroon Sukhera. Release v0.18.0, 9 November 2026.
Follow Steps A–G in order. The guided setup waits for owner confirmation after each
step. No real credentials, database provisioning, migration or provider test is
implied by the merged code. Never paste secrets into chat, screenshots, PRs or Git.

## A — Create the dedicated database

1. Open https://supabase.com/dashboard and select an organization on the **Free**
   plan. Do not add this project to an existing paid organization. Free eligibility
   includes a limit of two active projects; if unavailable, stop rather than upgrade.
2. New project: `webix-lead-desk`; choose **East US (North Virginia)** / `us-east-1`.
   Generate the database password in your password manager and save it there.
3. Project Settings → Data API (or Integrations → Data API → Settings): turn the
   Data API off. If the control differs, remove `public` from exposed schemas.
   We use SQL, not Supabase Auth, Storage or REST. No anon/service_role keys are used.
4. Connect → Session pooler: port **5432**, IPv4 pooler hostname. Copy privately,
   switch `postgresql://` to `postgresql+psycopg://`, insert the URL-encoded password,
   and include `sslmode=require` (use `&` if a query already exists).
   Percent-encode reserved password characters; do not send the URL for checking.

Shape only: `postgresql+psycopg://postgres.<ref>:<encoded-password>@<pooler-host>:5432/postgres?sslmode=require`.

Session pooler is the default for both processes. Direct Supabase hosts are rejected
by this release to avoid Free IPv6 compatibility issues. Transaction port 6543 is
supported only with `DATABASE_PREPARE_STATEMENTS=false`, which passes
`prepare_threshold=None` to psycopg. Use the session pooler for this setup/migrations.
For stronger TLS identity verification, install the provider CA privately and use
`sslmode=verify-full`; `require` encrypts without checking the hostname.

[ Screenshot placeholder A: project name, Free plan and region; redact project credentials. ]
[ Screenshot placeholder A2: Data API disabled; never show keys or connection strings. ]

**Stop here and confirm A before continuing to B.**

## B — Private keys on the PC

If starting a new checkout, `uv run python scripts/setup_local.py` creates `.env`
with random ENCRYPTION_KEY, SESSION_SECRET and BACKUP_KEY without printing them.
It preserves an existing file. Copy the keys privately to your password manager;
retain any existing ENCRYPTION_KEY that already protects mailbox/OAuth credentials.
The three generation commands in the owner's specification also work in a private
local terminal. Never run them in a chat-visible terminal or share their output.

ENCRYPTION_KEY must match on Vercel and PC. BACKUP_KEY stays on the PC and in the
password manager, separately from backup files. The PC configuration also requires
a SESSION_SECRET (created by setup_local); it can differ from Vercel's because the
PC is only a worker. Generate a separate Vercel SESSION_SECRET privately.
Losing ENCRYPTION_KEY requires re-entering encrypted provider credentials; losing
BACKUP_KEY prevents opening the encrypted backups.

**Confirm B before C.**

## C — Vercel Production configuration

Use the existing `webix-lead-desk` project; keep FastAPI and root `./`.
First read its actual Production domain in Settings → Domains. Do not guess it.
Set these private environment fields for Production:

| Variable | Value |
|---|---|
| APP_MODE | VERCEL |
| DATABASE_URL | Session pooler URL from A; replace any old Neon value |
| DATABASE_PREPARE_STATEMENTS | true (session pooler) |
| ENCRYPTION_KEY | Original shared key from B |
| SESSION_SECRET | Private Vercel session key from B |
| COOKIE_SECURE | true |
| ALLOWED_HOSTS | JSON array containing only the actual domain |
| PUBLIC_BASE_URL | https:// followed by the actual domain |
| LIVE_SEND_ENABLED | false, always |
| EXTERNAL_INTEGRATIONS_ENABLED | false |
| PREVIEW_ENABLED | false |
| DISPLAY_TIMEZONE | Asia/Karachi |
| SENDING_TIMEZONE | America/Edmonton |
| OPENAI_ENABLED / LINKEDIN_POSTING_ENABLED | false |

Do not put BACKUP_KEY or BACKUP_DIRECTORY on Vercel. Redeploy after saving the
variables. Until step E migrates the database, readiness is expected to fail.
Preview deployments must use an isolated test database, never these Production
secrets. Session pooler client capacity is shared across Vercel instances: NullPool
closes each checked-in connection; it does not cap total concurrent invocations.

[ Screenshot placeholder C: variable **names only**, values completely hidden. ]

**Confirm C before D.**

## D — PC worker configuration

Edit the private project `.env` in a local editor. Keep the local SESSION_SECRET
created in B and set:

| Variable | Value |
|---|---|
| APP_MODE | PC |
| DATABASE_URL / ENCRYPTION_KEY | Exactly the same as Vercel |
| BACKUP_KEY | Private backup key from B |
| BACKUP_DIRECTORY | Owner-chosen absolute folder, e.g. C:/WebixPrivateBackups |
| BACKUP_INTERVAL_DAYS / BACKUP_KEEP | 7 / 8 |
| LIVE_SEND_ENABLED | false until separately approved test sends pass |
| PC_KEEP_AWAKE | true |
| OPENAI_ENABLED / LINKEDIN_POSTING_ENABLED | false |
| PREVIEW_ENABLED | false |

Protect the backup folder with your Windows account permissions. Keep an additional
private copy on another device; local disk failure must not lose every backup.
The worker's pool is 3 connections, zero overflow, pre-ping, recycle 300 seconds.
Backoff applies to connection establishment. Interrupted transactions are not
replayed; uncertain SMTP results retain the existing sender/message identity.

**Confirm D before E.**

## E — Initialize from the PC

For an empty dedicated project (stop if importing an existing workspace):

```powershell
uv sync --frozen --group dev
uv run lead-desk migrate
uv run lead-desk create-user --email <your-email> --name "Haroon Sukhera"
uv run lead-desk check
```

Type the account password only in the hidden prompt. Migration 0020 enables RLS
on every application table, including Alembic's version table, with no policies.
The owner Postgres role bypasses RLS; ordinary Data API roles see no rows. Do not
switch the SQL connection to a non-owner role without designing explicit grants.
Future migrations must enable RLS on new tables; CI checks the complete ORM set.
Use the empty-target restore procedure instead of creating an owner when importing.

**Confirm E before F.**

## F — Start one Windows worker

```powershell
.\scripts\worker.ps1
# Once this works, in an Administrator terminal:
.\scripts\install-autostart.ps1 -WorkerOnly
```

Enter the real Windows account credential in the Windows prompt only. The task runs
at startup, signed out as well as signed in, restarts after one minute on failure,
and catches missed starts. Existing local Web tasks are not removed automatically;
stop/disable one if present because the hosted dashboard replaces it.

Keep the PC powered on with Windows sleep disabled over the intended business window.
The existing awake helper prevents sleep while the running worker is in Calgary's
weekday 07:30–17:00 window; it cannot wake a shut-down/sleeping PC. As of 9 November
2026 this is **18:30–04:00 Pakistan time** (09:00–17:00 sending is 20:00–04:00).
Do not use 21:00–05:00 as the complete window. The app uses bundled timezone rules,
not the PC clock's assumed offset. On Supabase use a current Postgres image with
the October 2026 timezone update; app scheduling itself is calculated in Python.

**Confirm F before G.**

## G — Verify before live outreach

1. Actual domain `/health/live` and `/health/ready` both return OK after initialization.
2. Laptop and phone sign-in/sign-out work. Reviewer accounts cannot open owner Settings.
3. Queue a health job in the dashboard; the PC worker completes it.
4. Stop the worker; dashboard remains available and shows offline. Restart: status clears.
5. Run `uv run lead-desk backup --online --file C:/WebixPrivateBackups/manual-check.wld`.
   Workspace protection shows its timestamp only after file write/fsync succeeds.
6. Table Editor shows RLS on all application tables; Data API remains off. RLS warnings
   refresh at startup and then at most every 60 seconds per dashboard instance.
7. Perform the restore rehearsal below. Keep LIVE_SEND_ENABLED=false throughout.

[ Screenshot placeholder G: synthetic/empty workspace protection and completed health job. ]

## Backup schedule and restore rehearsal

With BACKUP_KEY and BACKUP_DIRECTORY configured, the worker takes its first backup
on its next loop, then every 7 days after success. If the PC is off it catches up once
when it starts. A durable one-hour reservation prevents duplicate concurrent backups
and delays retries after failure. Only this workspace's automatic filename prefix is
rotated to the last 8 after a successful write; manual/unrelated files are untouched.
Dashboard receipts contain timestamps, hash and size, never the PC path or keys.
No backups run in Vercel. Older-than-8-day/missing/failed backups show an owner warning.

Online backups use one consistent SQLite/Postgres snapshot and the existing Fernet
format. The live workspace is unchanged. The restored copy is in maintenance, with
unfinished jobs failed, unsent transmissions uncertain, LinkedIn tasks/posts held and
sequences closed. This also prevents stale queued work from automatically re-sending
work completed on the original workspace after the snapshot. Reconcile actual Sent
and LinkedIn activity before selectively rebuilding any pending work. Approved copy,
message identities, sender locks, suppression and immutable evidence are retained.
An online backup is disaster recovery, not seamless queue resumption.

For rehearsal, use the same release and a **temporary local empty SQLite database**
or isolated PostgreSQL database, with a separate private configuration. Copy the
original ENCRYPTION_KEY and BACKUP_KEY privately, disable every integration, leave
BACKUP_DIRECTORY unset and never point the rehearsal at production. Run migrate,
then `lead-desk verify-backup --file <path>` and
`lead-desk restore --file <path> --offline-confirmed`. Compare row counts/evidence,
sign in locally and confirm maintenance stays on. Keep the source running only if
this isolated rehearsal is strictly read-only. For an actual cutover, stop the source
and follow [move-to-vps.md](move-to-vps.md); never run two active restored copies.
The default raw backup ceiling is 100 MB; exceeding it fails clearly. Review PC RAM
and explicitly increase BACKUP_MAX_BYTES before a growing workspace exceeds that
limit. Fernet snapshots are memory-bounded by this setting, not streaming archives.

## Capacity, activity and provider choice

Supabase Free currently lists 500 MB database storage, 5 GB egress and one-week
inactivity pausing; it does not include downloadable automatic backups. Monitor its
actual dashboard usage, including egress from repeated backups. App warnings use
`pg_database_size`, default threshold 400,000,000 bytes, with largest app tables
including indexes. Billing size and catalog estimates can differ. The app does not
measure egress or the provider's inactivity timer. A five-day worker gap is an
operational warning; dashboard activity also accesses Postgres. No uptime guarantee
or artificial keepalive is added to prevent free-project pausing.

Neon remains compatible as an optional PostgreSQL provider. Its Free quota is
**100 CU-hours**, not 100 wall-clock hours. At a constant 0.25 CU, that corresponds
to about 400 active hours; actual usage depends on compute size. Frequent polling
prevents five-minute idle suspension. Re-check current quotas before choosing it.

References checked 9 November 2026:
- https://supabase.com/docs/guides/database/connecting-to-postgres
- https://supabase.com/docs/guides/api/securing-your-api
- https://supabase.com/docs/guides/database/postgres/row-level-security
- https://supabase.com/pricing
- https://supabase.com/docs/guides/platform/backups
- https://supabase.com/changelog/tzdata-local-time-vancouver-edmonton-casablanca
- https://neon.com/pricing
