# Acquisition setup and acceptance (v0.12)

## Start a campaign

1. Run the new Alembic migrations in maintenance mode after taking a backup.
2. Keep `OPENAI_ENABLED=false` until the owner confirms the linked API organization's
   credit balance, expiry and audit/draft model prices. Record funding in Settings.
3. In Settings → Lead discovery, enter the verified web-search price **in USD per
   call**, monthly search budget and call caps. Daily default: 60. Monthly and
   monetary limits start at zero. The search budget includes tool and model usage
   and also consumes the overall AI ledger. Audit spending stops at 90% of funding.
4. Configure the sender identity and lane. Automatic drafting uses the single enabled
   sender in that lane; with multiple senders, choose explicitly in Write a draft.
5. Create a campaign with industry, city and offered services. Queue one discovery
   batch. Default: five candidates per batch, at most ten batches per submission.
   More batches stop when no new businesses arrive, a quota is reached, or a provider
   request is uncertain. They never constitute a promise of fifty qualified leads.
6. Run the existing PC or VPS worker. Vercel serves the dashboard; a permanent worker
   still runs separately. The worker and dashboard use the same database. SQLite
   remains local-only; use the existing PostgreSQL/Neon URL for a split deployment.

The paid search boundary uses `OPENAI_AUDIT_MODEL` with Responses `web_search`,
`max_tool_calls=1`, source inclusion, bounded structured output and the independent
`OPENAI_SEARCH_MAX_OUTPUT_TOKENS` ceiling. Price settings are owner-entered rather
than hardcoded. Reservations conservatively cover a 128k search context, request
framing, output and one tool call. Verified usage releases unused cost; missing or
uncertain usage retains the reservation. A timeout is never a free retry.

Company searches are deduplicated for 90 days, including uncertain attempts. Broad
city queries exclude known businesses and deduplicate identical queries for 24
hours. Source identity and official domain checks prevent duplicate outreach, while
separate franchise branches sharing a domain require review. No directory or social
profile is accepted as an official website. The shared fetcher blocks private IPs,
rebinding and unsafe redirects. All website/model text remains untrusted data.

## Reviewed observations and no-website businesses

The rule score is independent of AI. Target industry +2, confirmed ads +2, confirmed
weak mobile +2, confirmed no website +3, response/booking gap +2, ten or more reviewed
Google reviews +1, and mature software/strong modern site −3. No-website and weak-mobile
points do not stack. Scores are bounded to 0–10. Default bands: 7+ Contact now,
4–6 Later, below 4 Skip. Settings publishes immutable rule revisions and queues free
recalculation. Each snapshot retains changes, source evidence and policy version.

A search omission is **unknown**, not proof of no website. A reviewer must record
the listing source and confirm the status. Rating, reviews, photos, hours, recent
posts and social activity are explicit observations, never fabricated analytics.
Select the website + Google Business Profile package from the active Offer Library.
The no-website lane appears separately in Results and Leads.

## Calls and email consent

Today → Calls contains due phone leads without usable email plus confirmed
no-website businesses. Suggested call times are recommendations, not inferred
customer availability. Staff make calls themselves. The app never dials, places
AI-voice calls, submits social DMs or contacts a prospect during research.

The human records an outcome and original notes. A callback/meeting requires a
future Calgary time. Interested requires the exact agreed email and explicit
consent note. Consent stores the actor, time and purpose; DNS and owner approval
are still required before delivery. "Not interested" suppresses the company and
known phone/addresses and cancels pending work. A later-discovered email cannot
bypass that company stop. Reviewers cannot open Settings or approve messages.

Optional AI cleanup stores suggested notes, next action and email copy. A new,
consented contact with one configured sender can become an unapproved draft. Existing
locked conversations retain their routing and require review through the existing
message editor; suggested copy does not overwrite or resume them automatically.

## Provider retention and live acceptance

Google Places is OFF by default. Its owner-set caps and encrypted credential are
separate from OpenAI funding. The adapter requests a minimal field mask. Raw Google
business names, addresses, ratings and reviews are not retained as a cached listing
database. Place IDs may be retained; a business is imported only after its identity
is independently confirmed on a fetched official page. Existing imported identities
are preserved. Google-only missing-website businesses need another permitted source
or manual review. The 90-day search marker does not grant a right to cache Google
content. See [Places policy](https://developers.google.com/maps/documentation/places/web-service/policies).

OpenAI citations are displayed as clickable field-specific links. See the
[Responses web-search guide](https://developers.openai.com/api/docs/guides/tools-web-search).
No provider pricing is frozen in code; verify it before entering rates.

Offline tests cannot confirm live search relevance, account/model availability,
mailbox authentication, folder behavior, provider rate limits or email delivery.
After owner setup, use a synthetic provider check and approved internal test inbox
before the existing pilot. Production database migration and real sender acceptance
are separate from a successful Vercel build.

## Journey journal

`lead_events` is append-only at the database level. Events link original source,
audit, draft, approval, delivery, reply, call and pipeline records. Repeated business
keys do not append duplicates. Migration 0014 backfills historical events and initial
free scores; old free-text observations remain unknown until structured review.
The stage bar, current/next action and timeline are projections, not another editable
status field. Draft links display the immutable revision and an escaped text diff.
Operational delivery tables continue to enforce sender locks and approval checks.
