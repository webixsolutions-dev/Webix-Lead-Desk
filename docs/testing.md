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

## v0.11 regression coverage

The offline suite covers all three providers' SMTP outcomes, read-only IMAP and different
SPECIAL-USE Sent names; sender lock and reply cancellation at all four follow-up positions;
plain and structured DSNs; negative/positive/uncertain reply phrasings; exact-address DNS cache
expiry, allowed roles and blocked no-reply; campaign offsets, delayed actual acceptance and
Hostinger rolling limits; concept/follow-up slot separation; partner guidance/local meetings;
six-tab Settings sections; audit Batch identity/expiry/partial rows and synchronous drafting.
Full tests use loopback provider fakes or mocks. CI separately checks PostgreSQL migrations
and populated SQLite-to-PostgreSQL recovery, with Windows setup smoke only for main/PRs.
Real-provider credentials, DNS propagation, localized SPECIAL-USE flags and deliverability
still require owner-controlled acceptance. Synthetic tests do not certify live account setup.


## v0.12 acquisition regression coverage

Mock Responses web search and public HTTP: official-site acceptance, directory and
identity rejection, private redirects, source citation validation, cap-before-call,
uncertain billing retention, concurrent cache reservations, any-city identity
deduplication, empty-batch termination, Places opt-in/retention and worker chaining.
Offline tests also cover rule score history, listing review, no-website consent,
call rejection suppression, future callbacks, reviewer restrictions, journey
filters, read-only event triggers and populated v0.11 migration/backfill.
UI review uses synthetic static renderings; no real prospects, mailboxes or API
credentials are included in captures. Live provider acceptance remains separate.


## LinkedIn L1

Offline regression: normalized identity aliases, conflicting matches, concurrent
member assignment, one member/two active people per company, four-channel reply
cancellation, immutable touches, permanent stops, expired cooldown review, and
owner/reviewer HTTP authorization. Search proposals are tested with a fake Responses
provider; no LinkedIn or email network requests occur. Visual review uses fictional
people and member identities. Live team identity verification remains an owner task.

## LinkedIn L2

Offline tests cover concurrent quota/person-day reservations, warmup ceilings, weekly
and pending caps, current owner limits, three-batch catch-up, DST, five-day stale holds,
expired action claims, member isolation, and the actual SMTP dispatch deferral using
fake transports. PowerShell CI validates startup script syntax without installing tasks;
actual Windows sign-out/reboot acceptance remains an owner-PC check. No live provider
calls are made and no real LinkedIn action is performed.

CI exposed different OS timezone databases. Current bundled tzdata is now used across
all application clocks, display, callbacks and quotas. Regression checks cover Alberta's
November 2026 permanent UTC-6 transition and historical UTC-7 winter behavior.


### LinkedIn L3 local verification

360 offline tests passed, with the two PostgreSQL-only checks left to CI. Exact
approval and database mutation guards, wrong-member isolation, monthly category/total
caps, uncertain completion, replay prevention, requested response separation and
90-day/12-month cooldowns are covered. Synthetic desktop/mobile browser review checked
readable copy, identity/opt-out retention and manual-copy fallback when clipboard access
is unavailable. Real LinkedIn execution was not performed.

## L4 public posting

Local full suite: 376 passed, two PostgreSQL-only tests skipped locally; CI runs them.
Offline post/media/OAuth cases and synthetic desktop/mobile review are included.
Coverage: overall 85.17%, jobs 80.74%, sending 91.77%, inbox 94.85%.
Real OAuth, codec processing and native LinkedIn posting remain owner acceptance tests.


## L5 results and learning

Final local full suite: 387 passed, two PostgreSQL-only tests skipped locally; CI
runs these separately. Coverage: overall 85.37%, jobs 80.88%, sending 91.77%,
inbox 94.85%. Regression tests cover distinct-person weekly cohorts, temporal
reply attribution, exact-draft angle provenance, comparable 50-contact minimums,
49/50 request and 19/20 percent pause boundaries, preserved owner-review history,
immutable observations, latest cumulative post totals, role isolation, CSRF,
maintenance, fortnight proposal idempotency and the actual offline worker tick.
A 50-contact fixture verifies batched report queries stay bounded. Synthetic
desktop/mobile renderings were reviewed; no real account or prospect was contacted.

Limitations: analytics and SSI are member-entered observations; historical rows
without exact provenance remain unknown. The acceptance pause conservatively
includes pending invitations. An observed leading angle is not a causal finding.
Real provider, owner-PC startup and hosted database acceptance remain setup tasks.
