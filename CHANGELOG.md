# Changelog

## 0.30.7 — Campaign email signatures

- Allow an owner to use a separate signature for a campaign with an assigned sender address.
- Draft approval and delivery recheck the effective signature. Mailbox tests use the mailbox default.

## 0.30.6 — Evidence-led service qualification

- Apply an owner-published service qualification playbook to assessments and campaign drafts.
- Record support, intake, self-service and commerce link paths in public website audits.
- Screen current catalogue services across each business. Services outside the campaign appear as review alternatives.
- Provide a copyable lead qualification brief for manual ChatGPT use without an API call.

## 0.30.5 — Website qualification and shared campaign sender

- Record per-page public SEO evidence and HTML response timing. An optional PageSpeed Insights credential enables one Lighthouse mobile lab test per fresh audit.
- Pause new requests to a failed API provider until the owner reviews the issue and resumes the provider.
- Support an exact shared sender for an explicitly configured campaign and keep UAE and New Zealand meeting hosts private in opening emails.

## 0.30.4 — Campaign planning and sender identity

- Put Campaigns first in the Leads views, add paused UAE drafts and display a campaign's required sender.
- Enforce the configured sender domain and brand in drafting, approval and delivery.
- Show private, unassigned LinkedIn post copy before a profile is attached.

## 0.30.3 — Campaign drafts and guidance labels

- Label Playbook history by its industry, conversation, LinkedIn or named campaign scope.
- Allow New Zealand campaigns to be saved and edited as paused drafts while local sending is unavailable.

## 0.30.2 — WhatsApp idle polling

- Treat WhatsApp's empty HTTP 204 update response as a normal idle poll, keep the last update offset and continue checking for pairing messages and queued notifications.

## 0.30.1 — WhatsApp reaction shortcuts

- Show tap-friendly reaction choices on every actionable full-detail draft notification.
- Accept 👍 to approve and schedule the exact saved revision, 👎 to reject it, or ✏️ to open its editor.
- Bind every reaction to the paired owner and exact outbound WhatsApp message ID; stale, changed, expired, summary-only and already-used notices stay blocked.
- Keep typed APPROVE/REJECT/EDIT codes as a fallback; unsupported reactions, voice notes and free-form messages remain non-authorizing.

## 0.30.0 — Native WhatsApp owner agent

- Owner-only encrypted API-key settings and one-time private-chat pairing.
- Paced draft and incoming-email alerts with summary-only/full-detail sharing choice.
- Explicit per-version APPROVE/REJECT commands; EDIT opens the existing editor/rewrite flow.
- Replay/expiry/stale-review checks reuse email approval and scheduling policy.
- Durable poll cursor, conservative uncertain-send handling, immediate local disconnect.
- Paired sessions and review codes do not transfer in backups; reconnect after restore.


## 0.29.0
- Add optional owner-only authenticator enrollment, password-first sign-in challenges and private single-use recovery codes. Existing accounts remain unenrolled.
- Require password and existing factor for security changes; revoke other devices while preserving the current session. Never make MFA a sending gate.
- Serialize challenge/code use, prevent replay, retain rate/CSRF checks, and keep factors enabled across password resets.
- Add private RLS-protected security tables in migration 0025 and show approximate device descriptions and last-seen times.
- Keep the expanded workspace-status card within mobile viewports.

## 0.28.0
- Prepare a bounded meeting brief with each booking, including exact-contact email context, reviewed facts, industry questions and relevant bundle reasons.
- Confirm completed meeting notes, prepare immutable editable proposals with owner-entered prices, approve exact revisions and export branded Word documents.
- Record actual manual proposal delivery separately from export; add day 2/5/9 owner review reminders and Meeting held, Proposal sent and Negotiation stages.
- Reject stale proposal reviews after notes, identity, booking or conversation changes. Keep automatic messaging disabled for proposals and follow-ups.
- Migration 0024 creates private meeting/proposal tables with default-deny PostgreSQL RLS.

## 0.27.0
- Block AI conversation suggestions with repeated, irrelevant or premature copy; show human warnings with recorded owner overrides. Changed recipient context invalidates review.
- Add ask-first, named research, dated revisit and no-opportunity outcomes, including owner entry and Today research work.
- Add discovery-only services and five reusable internal bundle templates; exclude bundles from first-contact copy and campaign service choices.
- Preserve explicit owner quotation permission on email and LinkedIn replies. Known provider spend remains charged when a suggestion fails checks.
- Migration 0023 adds offer availability, type and components; inbound AI voice remains discovery-only.

## 0.26.0
- Bring replies, bookings, drafts, calls and research into an ordered Command Center with in-place review.
- Consolidate navigation into six sections, recipient conversations, three settings groups and canonical bookmark redirects.
- Show recorded activity and distinct-business campaign progress with read-only 20-second polling.
- Preserve exact approval gates, staff isolation and Calgary sending windows; restrict Open Calgary discovery to Calgary campaigns.
- Reuse one editable desktop dashboard default.

## 0.25.0
- Improve workspace typography, form controls and contrast, with Normal/Large text preferences.
- Add owner-only AI allowance and monthly usage views, separating confirmed spend, pending reservations and expiry without resetting credit.
- Add CI browser contrast/overflow checks and synthetic desktop/mobile captures.

## 0.24.0 — Business-first reviews, conversations and bookings

- Review business facts, source links, audit gaps, service hypotheses and the exact email together; approve selected exact revisions in batches up to 100.
- Keep manual approval as default. Optional owner delegation applies only to new cold outreach in selected campaigns. Replies, quotations and concepts remain owner-only; disabling delegation revokes pending automatic approvals.
- Add reply-aware AI rewriting with bounded recipient-specific history and optional owner-approved quotation content.
- Add timezone-aware proposed bookings, explicit confirmation, conflict checks and rescheduling.
- Add named LinkedIn chat/activity views using recorded human invitations, acceptances and replies; no unsupported inbox or invitation API is implied.
- Add 26 editable industry references, conversation/LinkedIn guidance scopes, tool/booking markup signals, balanced evidence and ranked service alternatives. Strong sites no longer reduce non-website campaign opportunities.
- Migration 0022 adds private booking records and approval provenance. Upgrade the worker before restarting. AI funding, campaigns and live delivery are not enabled by this update.

## 0.23.0 — Reusable service offers and scoped proof

- Save multiple packages per service with optional custom-quote pricing, explicit currency and billing period.
- Keep uncertain prices empty, preserve existing CAD rates, and reject fractional-cent or non-finite amounts.
- Match verified proof to campaign service and optional industry, with explicit project attribution and reviewed edits.
- Select saved service families in campaign targeting; show country at creation and city/state/province labels.
- Clarify global, partner and campaign guidance and optional local meeting profiles.
- Migrate through Alembic; keep real workspace service data out of the repository.


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

