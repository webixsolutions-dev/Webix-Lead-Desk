# Delivery plan

On 8 November 2026 the owner authorized sequential implementation and merging of all phases after passing checks. Work remains single-agent, one phase branch at a time, with a PR, changelog and tests for every phase. No new spending is authorized. Exact outgoing messages still require approval in the application.

## Runtime and boundaries

Keep Python 3.12, FastAPI, Jinja/HTMX, SQLAlchemy and Alembic. Vercel hosts the web process, PostgreSQL holds hosted data, and a separate PC worker performs bounded research and mail jobs. PC-only mode keeps SQLite. VPS mode uses the same code with PostgreSQL and a supervised worker. Changing modes never migrates data automatically.

The original architecture remains the guide. Add domain model modules under app/db/ as the schema grows; keep identity/job models intact. Register models through app/db/__init__.py. Compare the installed Alembic revision with the current migration head instead of hardcoding Phase 1.

## Sequential implementation

1. Hosted setup: secure configuration and database/account bootstrap. The connected Neon organization currently reports plan `build`; defer provisioning until no additional charges can be verified. Vercel environment writes work when using the known project ID with the connected account's scope. APP_MODE has been set; no database or owner login has been created.
2. Sources: campaigns/runs, companies/domains, provenance, contacts/bases, global suppression and channel reservations. CSV uploads are bounded and validated atomically. Open Calgary uses a fixed permitted endpoint; domain matches remain reviewable. Test duplicate reimports, shared domains, suppression, authorization and migrations.
3. Audits: bounded public HTTP adapter with DNS/IP pinning, redirect/robots controls; structured extraction, evidence/finding records, manual social/ads observations and optional budgeted AI. No API charges without verified funding and configured limits.
4. Drafts: versioned Playbook, offers/proofs, immutable message versions, editing/rewrite notes, skip reasons and exact-envelope approval. Manual drafts/imported AI drafts work without paid API access.
5. Delivery: encrypted connections, SMTP/IMAP wrappers, inbox-first dispatch, sender/thread locks, reservations, ramp/window limits, bounce/suppression stops, uncertain-send holds and fake-provider failure tests. Live delivery remains off until sender identity, contact basis, credentials and test acceptance are complete.
6. Pilot: readiness checklist and up to ten researched prospects; individually reviewed first sends and one follow-up only after owner approval. Unconfigured live-provider acceptance stays explicitly pending.
7. Pipeline: reply review, call cards, assignment, meetings, outcomes and owner-requested concept demos. Approved demo delivery uses the same conversation controls.
8. Partners/results: separate brand/mailbox lane and category reporting; no Gmail cold outreach.
9. Learning: evidence-backed proposals from edits/results, owner-only application and version rollback.
10. Operations: backup/restore and SQLite-to-PostgreSQL tooling, systemd templates, health checks and cutover rehearsal. Do not provision or move to a VPS without its credentials and an actual cutover requirement.

## Acceptance and owner handoff

Each phase gets offline tests, typing/lint, migration checks and secret scanning. Provider acceptance is separate from tests and never substituted with synthetic success. UI review uses fictional records only. At the end list incomplete external checks and the smallest set of owner actions: database cost/account selection if unresolved, owner credentials, Titan and optional AI/Firecrawl keys, legal sender details, verified domain/mailbox settings, test inboxes and approval of actual prospects/messages. Secrets go only into private environment/settings fields.

Phase 2 was merged as PR #3 after all Linux, Windows, PostgreSQL and secret checks passed.
Phase 3 adds research and optional AI budgeting; configured provider and PC acceptance are
owner setup items, not credentials embedded in the repository.
