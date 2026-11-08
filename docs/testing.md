# Verification

Automated tests use synthetic accounts and isolated databases. Python socket connections to non-loopback addresses are rejected during tests. No real prospect emails, mailbox connections or OpenAI calls are permitted.

## Local suite

```sh
uv run pytest -m "not postgres" --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=80
uv run python scripts/check_coverage.py
```

Coverage of at least 80% is enforced separately for jobs, sending and inbox, alongside the overall threshold.

The suite verifies authentication, CSRF, session expiry/revocation, role enforcement, rate limits, saved settings, stale edit conflicts, preview isolation, HTML escaping, handler allowlisting, worker ownership, idempotent jobs, bounded retries, crash recovery and stale claim rejection. CLI tests exercise migration, account creation/reset and worker operation.

## PostgreSQL

Use a disposable local PostgreSQL database named `lead_desk_test`. The test drops and recreates the test schema. Never use a production or remote database URL.

```sh
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/lead_desk_test uv run pytest -m postgres
```

The CI service uses a transient local test database and no real credentials. Without TEST_DATABASE_URL the PostgreSQL test is explicitly skipped, not passed.

## UI and platform checks

- Verify login/logout, safe preferences, all implemented sections and worker status with synthetic data.
- Verify narrow/mobile layouts, navigation and keyboard focus.
- Attach screenshots to the phase PR. Do not commit screenshots or database files.
- Native Windows setup is checked in its CI job. Linux-only tests do not establish Windows verification.
- A static screenshot preview demonstrates layout only. It does not prove browser authentication, database connectivity or worker execution.

## Delivery and recovery acceptance

The offline fake SMTP/IMAP tests cover same-sender/thread follow-ups, cancellation on any reply, global opt-out, bounce pause, ramp limits, Calgary window/DST, immutable approval and uncertain SMTP handling. Automated tests remain offline. Separately approved test-inbox acceptance is a supervised exercise, not a pytest test.


Phase 10 adds authenticated backup/restore, wrong-key/tampering rejection, empty-target protection, maintenance before DATA, crash holds and local CLI recovery. PostgreSQL CI also transfers populated synthetic SQLite data and compares every restored table, then cleans only its isolated test database.
