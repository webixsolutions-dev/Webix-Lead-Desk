# Changelog

## 0.22.0 — 2026-11-09

- Per-mailbox HTML signatures with a safe preview and a generated text alternative.
- Signature revisions are included in exact draft approvals and fixed-message test approvals.
- Changed signatures block old approvals; diagnostics require a signature-capable worker.
- Multipart email preserves the outreach identity/unsubscribe footer and exact Sent reconciliation.
- Isolated authenticated previews remove scripts, editor tracking attributes and unsafe URLs/CSS.
- Existing settings storage and encrypted desktop configuration stay compatible; no schema migration.

## 0.21.0 — 2026-11-09

- Settings now previews and queues a fixed test email to an owner-approved test inbox.
- Updated Windows worker advertises a separate diagnostic capability while outreach stays off.
- Encrypted exact-message approval, durable attempt markers, three daily slots and duplicate protection.
- Uncertain results never retry; the owner can record actual receipt after checking the test inbox.
- Existing database schema and encrypted desktop settings remain compatible; no migration required.

## 0.20.0 — 2026-11-09

- Focused Today page with a next action, real counts and concise worker/sending/backup status.
- Six clear navigation destinations, stronger contrast and responsive spacing.
- Compact lead filters, expandable import tools and readable business cards.
- Settings grouped into email, writing, discovery, learning, preferences and system sections.
- Keyboard-friendly mobile navigation and deep links that reveal collapsed sections.
- Existing worker, credentials, sender approval gates and database schema remain compatible.

## 0.19.0 — 2026-11-09

- Bundled Windows installer and red/white desktop worker controls.
- User-scoped encrypted settings, recovery key access and optional sign-in startup.
- Cooperative Stop finishes in-flight work; existing sender/worker fences unchanged.
- Windows package smoke, upgrade/uninstall checks, downloadable installer and checksum.


## 0.18.0 — 2026-11-09

- Supabase session-pooler defaults, SSL/port guards, Vercel NullPool and bounded worker pool.
- Connection-only retry/backoff with existing send identity/uncertainty fences preserved.
- Migration 0020 enables default-deny RLS on every application table.
- Weekly encrypted online PC snapshots, scoped eight-file retention and restore review holds.
- Owner backup-age, RLS drift, database-size and five-day worker-gap diagnostics.
- Step-by-step hosted setup and worker-only Windows autostart.


## v0.17.0 — LinkedIn L5: observed results and reviewed learning

- Scoped weekly profile cohorts with distinct people, explicit denominators and 30-day reply attribution.
- First-message angle/context comparisons; no candidate leader below 50 contacts per comparable variant.
- Automatic profile hold below 20% acceptance after 50+ invitations, with attributable owner resume.
- Immutable manual SSI, restriction and cumulative post snapshots; provider analytics are not assumed.
- Fortnightly inert Playbook proposals and approved guidance in future creative briefs.
- Publication-confirmation timestamps, batched provenance queries and offline regression tests.
- Original phases and LinkedIn L1–L5 code are complete; real owner/provider launch checks remain separate.

## v0.16.0 — LinkedIn L4: reviewed member posting

- Staggered calendar, distinct copy, warm-up post caps and immutable exact approvals.
- Encrypted session-bound OAuth, explicit member confirmation and seven-day expiry warning.
- Private 1 MB media chunks, SHA-256 verification, bounded PNG/JPEG/MP4 containers.
- Official Images/Videos/Posts adapter; durable submission marker and uncertain-result hold.
- Native copy/download fallback; video is upload plus caption only. No live provider calls.

## v0.15.0 — LinkedIn L3: exact review and manual member queue

- Immutable LinkedIn copy revisions and append-only owner approvals bind the recipient,
  real member, full identity/opt-out, reviewed evidence, angle and expiring contact basis.
- Manual member cards support start/open/copy/confirm, connection acceptance, factual
  replies and profile corrections. Unknown actions require owner reconciliation.
- Three-message progression, stale-task review, 21-day withdrawal hold, 90-day silence
  cooldown and retained 180-day ownership. Reply responses never restart cold sequences.
- Member access is limited to their queue and assigned company calls. Owner Outbox and
  Pipeline integrate LinkedIn tasks and real replies without fabricated email threads.
- Reviewed voice cards, optional budgeted copy improvement, free asset prompts and
  non-carrying monthly AI category caps under a $170 total ceiling.
- Current scope remains human messaging; official post publishing is the next phase.


## 0.14.0 — LinkedIn L2

- Persistent weekday planner, conservative warmup/daily/weekly quotas, catch-up slots and uncertain-action holds.
- Shared email/LinkedIn person-day reservations and serialized ten-member registration cap.
- Owner quota controls, member-scoped daily plans and Windows startup/sleep management.
- Bundled current timezone rules keep Calgary windows consistent across PC/VPS/Vercel after Alberta's November 2026 move to permanent UTC-6.
- No invitation or DM execution; exact-approved member actions arrive in L3.


## 0.13.0 — LinkedIn L1

- Add human-confirmed people, source-backed owner proposals and normalized identity aliases.
- Lock people and companies to one real staff member, with two active people per company.
- Record immutable cross-channel touches and cancel pending email, form and LinkedIn work on replies.
- Distinguish a new 12-month rejection cooldown from permanent opt-outs; retain legacy suppression.
- Add owner profile/people review screens and a budgeted, cached open-web owner finder.
- Preserve separately approved requested responses and agreed human callbacks after sequence cancellation.
- LinkedIn scheduling, message approvals and posting remain later L2–L5 phases.


## 0.12.0 — Discovery, human calls and lead journeys

- Add budgeted OpenAI Responses web-search discovery for any campaign city,
  official-site identity checks, per-field source links, 90-day company search
  deduplication, bounded batches and automatic audit/score/draft continuation.
- Add disabled-by-default Places New adapter, encrypted key and separate owner caps;
  retain place IDs and independently verified first-party data only.
- Add editable/versioned rule scores, listing reviews, the no-website lane and
  Offer Library selection. Unknown signals do not earn points.
- Add mobile/print human call lists, outcomes, exact verbal consent, company/phone
  suppression, and optional AI note cleanup with unapproved email suggestions.
- Restrict Settings and Learning to owners; reviewers retain call-list access.
- Add immutable lead events, historical backfill, derived stages, source-backed
  score history, Calgary/Pakistan timeline, activity filters and revision diffs.
- Add offline provider, quota race, migration, call consent and UI regression tests.
- No provider purchases, real prospect contacts or live delivery acceptance tests.

## 0.11.0 — Outreach review fixes

- Add Titan, Hostinger and Gmail per-mailbox settings, SSL/STARTTLS, provider readiness/limits,
  Hostinger rolling-24-hour counts and SPECIAL-USE Sent discovery.
- Separate message kinds and sequence positions; client days 3/7/12/20 and partner days 4/10
  are editable with two-day actual spacing. Preserve exact approval, sender/thread and stop guards.
- Add independent Luna audit/Sol drafting settings and optional durable audit Batch jobs;
  correct claimed subscription-credit guidance while keeping AI disabled by default.
- Classify negative/uncertain replies conservatively; separate delivery delays from permanent
  bounces, including plain daemon notices with body Message-ID correlation.
- Check syntax, role and DNS before approval with a 30-day address-bound cache; preserve
  permission evidence and recheck transport routing. No SMTP probes.
- Enforce prices/guarantees on all messages, no first-touch URLs and approved-only concept links.
- Consolidate six main tabs, full white sidebar, provider badges, Learning/Operations sections
  and partner/client filters with Results under Leads.
- Seed versioned partner guidance and owner-recorded local team members; guard in-person claims.
- Add Alembic migrations 0011/0012; restrict Windows CI to main pushes and PRs into main.
- No live sending, real prospect contact, provider purchase or hosting provisioning.

## 0.10.0 — Operations and cutover

- Added shared maintenance gates for web mutations, worker claims and final send checks.
- Added authenticated encrypted backups, private key setup and transactional empty-target restore.
- Preserved approvals, sender locks, suppression and uncertain-send identities; restored sessions are revoked and maintenance remains on.
- Added SQLite-to-PostgreSQL transfer, isolated CI rehearsal, VPS service templates and owner setup instructions.
- Added owner-only Operations status. Live Neon/Titan/PC acceptance remains pending; no resources purchased or prospects contacted.

## 0.9.0 — Reviewed learning

- Captured edits, draft rejections and recorded sales outcomes as inert suggestions.
- Added exact owner-reviewed application to versioned Playbook guidance, with concurrent-edit guards.
- Added restoration of earlier guidance as a new version while preserving approved drafts.


## 0.8.0 — Partners and results

- Added owner-reviewed Canada/US partner targeting and a shared five-new-business daily draft cap.
- Added versioned targeting edits and actual campaign/sector results.
- Added exact-copy manual contact-form review and globally exclusive first-contact channels.


## 0.7.0 — Pipeline and concepts

- Added human-reviewed opportunity stages, team call handoffs, meetings and CAD outcomes.
- Added immutable homepage, service-page, call-flow and portal concepts with explicit public-link approval and revocation.
- Kept concept email approval separate, with original sender/thread and final reply/revocation guards.


## [0.6.0] — 2026-11-09
- Add a reviewed 10-business pilot cohort and atomic five-first-contact cap.
- Add owner launch/pause/acceptance decisions without granting message approvals.
- Replace preview dashboard metrics with real records and report worker/sender readiness separately.
- Preserve explicit pending status for live provider and prospect acceptance.

## [0.5.0] — 2026-11-09
- Add encrypted, write-only mailbox and optional research connections.
- Add exact-revision scheduling, sender locks, immutable Message-IDs and one approved follow-up.
- Enforce Calgary windows, spacing, ramp/campaign limits, freshness and global channel/suppression checks.
- Read Titan inboxes, stop on every reply and pause after three distinct daily bounces.
- Hold uncertain SMTP/crashed-worker sends and reconcile only matching Sent evidence.
- Verify protocol behavior against local fake SMTP/IMAP servers; no live outreach enabled.

## 0.4.0 — Drafting and review

- Add immutable message revisions and owner approvals bound to exact routing/content fingerprints.
- Add manual ChatGPT briefs and optional personalized API drafting; all results remain unapproved.
- Add versioned global/campaign Playbooks, sender identity profiles, editable CAD offers and verified proof cards.
- Add contact verification records, hypothesis review and skip reasons; retain global suppression checks.
- Keep mailbox delivery disabled until Phase 5 safeguards and provider acceptance are complete.


## 0.3.0 — Website research

- Add bounded website audits with robots checks, same-origin redirects, pacing, transient retries and a 30-day cache.
- Store source-addressed facts and reviewer observations; discovered contacts remain unverified.
- Add optional structured AI hypotheses with evidence-ID validation and atomic, zero-default spending limits.
- Pin website connections to checked public IPs and renew worker leases during long research.
- Keep live email disabled; no paid provider or prospect was contacted during verification.


## 0.2.0 — 2026-11-08

- Add campaigns, targeting snapshots, atomic CSV imports and queued Open Calgary discovery.
- Add shared-domain review, source provenance, contact publication review and global suppression.
- Keep fictional examples separate from actual candidate records.
- Record owner authorization for sequential checked merges; tag verified phase versions through CI.

## 0.1.1 — 2026-11-08

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

