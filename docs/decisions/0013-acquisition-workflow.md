# Acquisition workflow (v0.12)

The v0.11 release remains immutable. Sections 11–15 extend it with discovery,
rule scores, listing reviews, human calls and an event-derived journey.

## Boundaries and storage

- `sources/enrichment/`: a common provider result, OpenAI Responses web search,
  optional Places New, first-party identity validation and 90-day search deduplication.
- `scoring/`: versioned evidence-based rules. Unknown signals contribute zero.
- `calls/`: human call records, explicit verbal consent and optional AI note cleanup.
- `journey/`: append-only events and derived stage/next-action projections.
- Tables: acquisition settings/history, quota buckets, search reservations/results,
  score policies/snapshots, call records/note revisions and lead events. All schema
  changes use Alembic and work on SQLite and PostgreSQL.

Provider selection defaults to OpenAI, but no billable call can run without the
global AI switch, a credential, owner-entered rates and caps, and verified funding.
Daily/monthly reservations happen atomically before calls. Uncertain outcomes retain
their reservations; neither retries nor another worker may repeat a paid search.
OpenAI web search uses the audit model, one tool call maximum, structured output,
clickable supporting sources and the existing overall AI allowance. Search requests
use the synchronous Responses endpoint; ordinary audits retain the batch option.

Places is disabled until separately enabled and configured. Only place IDs and
independently fetched first-party evidence are retained; transient Places listing
fields are not a permanent prospect database. The 90-day search marker does not
imply a right to cache provider content. Google attribution accompanies temporary
Places results. Reviewers can record their independently checked listing observations.

## Execution

The existing single-leased PC/VPS worker executes short durable jobs:
discover → enrich → audit → rule score → optional AI assessment/draft. Each step is
idempotent. Missing evidence, an ambiguous domain, a missing sender or exhausted
budget creates a visible hold. Email creation does not create approval. Vercel hosts
the authenticated dashboard; it does not run a permanent polling process.

Imports automatically queue enrichment for companies without a website. Discovery
uses campaign industry and city and never requires an owner-supplied lead list.
Domain deduplication requires a matching business identity; unrelated businesses
sharing a franchise domain stay separate and require review.

## Human decisions

No search result is proof that a business has no website. A reviewer confirms this
lane using source evidence. Its offer comes from the active Offer Library. Calls
are human-made and manual DM text is copy-only. Call rejection suppresses the whole
company as well as known addresses, preventing later email capture from bypassing
the stop. Verbal consent records the exact address, time, staff member and note.
Reviewers can operate Calls but cannot open Settings or approve messages.

## Journey

Transactional source/delivery/approval events are linked by stable unique keys into
`lead_events`. Existing records are backfilled in the migration. Database triggers
reject event updates/deletes. Stage bars, last/next actions and score histories are
projections of events, with explicit stopped reasons; operational send locks remain
in the delivery tables. No additional manually maintained journey status is added.

Verification uses mocked providers, synthetic mailboxes and fixtures, including
caps before calls, uncertain results, source validation, scoring history, consent,
suppression, reviewer access and portable migrations. Real provider acceptance is
a separate owner setup task, never inferred from offline tests.
