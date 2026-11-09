# LinkedIn channel architecture

The owner requested the supplied LinkedIn channel plan after acquisition v0.12.
Build L1–L5 on separate branches with green checks before each merge. Existing
authorization covers those merges, not real messages, posting, or developer terms.

## Boundaries

Keep FastAPI, server-rendered six-tab UI, SQLite WAL on PC and PostgreSQL on VPS.
Vercel remains a dashboard option connected to the same durable database; it does
not become an always-running worker. New modules live in `app/linkedin/`, models
in `app/db/linkedin_models.py`, routes in `app/api/linkedin*.py`, matching tests
in `tests/linkedin/`. All database changes use Alembic.

L1 adds people, identity aliases, proposed matches, real-member profiles, company
coordination, ownership, task foundations and unified touches. Unique identity
keys and serialized company decisions enforce one member per company and at most
two active people. Profiles are tied to existing staff users; no invented members.
Owner finding uses the existing budgeted Responses web search boundary and accepts
only source-backed proposals. Human confirmation is required to create a person.
No LinkedIn pages are fetched or scraped. Any logged reply stops company-wide
pending outreach; permanent opt-outs remain separate from temporary holds.

L2 adds conservative quotas, leases, timed batches, catch-up, startup scripts and
same-person/day touch reservations shared with email. Human tasks are only released,
never executed by workers. Local shutdown cannot reset quota or repeat actions.

L3 adds immutable LinkedIn drafts, exact owner approvals and the member-only mobile
queue. Keep email drafts/conversations intact: LinkedIn revisions use a separate
typed table linked to the same person, company and journey. This avoids making the
email conversation/recipient requirements nullable. A unified Outbox presents both.
Per-job budget caps supplement the existing funding ledger, with an overall $170
monthly ceiling; unknown usage remains reserved. Templates never claim API usage.

L4 adds post slots, media metadata, immutable approval hashes, scoped OAuth state
and encrypted member posting tokens. One wrapper owns LinkedIn HTTP. Publishing
sets submitting before the external call; uncertain results require human checking.
No automated replay of an ambiguous post. Local private media survives restart;
Vercel uploads require durable storage or the worker endpoint, never ephemeral disk.
Image/video upload and the versioned official Posts API remain disabled until real
developer configuration and each member's own OAuth authorization are supplied.

L5 adds funnel/angle/post metrics and proposed Playbook changes. Observed metrics
remain distinct from benchmark estimates; no automatic policy updates or promises.

## Evidence and operating choices

LinkedIn's official Share on LinkedIn and Posts API documentation supports member
posting with `w_member_social`; read permissions can require additional approval.
Use returned token expiry, not an assumed exact lifetime. Developer app acceptance
and member OAuth are owner tasks. Never collect passwords or session cookies.

CRTC guidance says commercial LinkedIn DMs can be commercial electronic messages:
role relevance alone is not consent. Before approval store a reviewed contact basis,
identification and an opt-out method. A short conversational body can be accompanied
by required identity/opt-out details; do not drop these to hit a character target.

The supplied vendor benchmarks are planning material, not verified performance
claims or guaranteed safe invitation limits. Hard caps are internal safeguards.

Sources: https://learn.microsoft.com/linkedin/consumer/integrations/self-serve/share-on-linkedin
and https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api
and https://crtc.gc.ca/eng/com500/faq500.htm
