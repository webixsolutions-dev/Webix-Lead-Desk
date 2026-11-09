# LinkedIn channel setup and acceptance

## L1 identity and coordination

Back up the database and run `lead-desk migrate` before starting this release.
Migration 0015 creates the person/profile/ownership/task foundation, backfills
historical touches and preserves company holds for historical replies. It does
not clear existing suppression entries. Touches reject update/delete in SQLite
and PostgreSQL, like the existing lead journal.

Register each real member against their existing active staff user under Settings
→ LinkedIn team profiles. Do not register multiple people under one user. No
LinkedIn password, cookie, OAuth credential or paid account is needed for L1.
The member limit is ten. The actual team identities are supplied by the owner.

On a business, open People & LinkedIn. Find possible owner uses the existing
configured OpenAI audit model and web-search budgets, with a separate 90-day
owner-search cache. It searches public web evidence and proposes up to three
profiles. Only source-backed public URLs are accepted, and nothing is fetched
directly from LinkedIn by the app. Uncertain paid calls are held, never replayed.

Human confirmation records the name, role, business source, explanation, presence
and optional published personal email. A generic company inbox is not a person.
Aliases deduplicate normalized profile URLs, personal emails and name/domain.
Ambiguous aliases and a person already attached to another company require review.
Saving a person is not proof of consent and does not create an outreach approval.

An owner assigns a person to a member. A company has one member and at most two
active people. Existing first-degree connections are explicitly marked warm.
Low/no LinkedIn presence prevents starting a LinkedIn sequence. Ended assignments
retain a 180-day lock; an owner can explicitly release routing with a reason.
Releasing routing cancels pending LinkedIn tasks and never clears suppression.

Every actual reply logs a cross-channel touch and cancels pending company work.
Email replies are linked automatically; LinkedIn, phone and form replies are
recorded by a human. Staff can report only their own assigned people. New "not
interested" outcomes create a 365-day cooldown. Only an owner can reopen an expired
cooldown with a new angle. "Stop"/unsubscribe is permanent. Existing legacy stops
remain preserved because the original evidence may not distinguish a rejection
from an opt-out. API jobs never interpret inbound text as instructions.

Cancellation does not erase a requested human callback or an exact, separately
approved response. An email explicitly requested on a logged call must use the
agreed contact, matching call evidence and a new reviewed draft. It does not
restart automatic follow-ups. Already transmitted email cannot be recalled.

L1 does not release invitations or messages and has no LinkedIn transport. L2 adds
the daily planner and quotas; L3 adds exact LinkedIn approvals and member queues;
L4 adds scoped official member posting; L5 adds reports and reviewed learning.

## Owner setup collected for later

- Real Calgary staff accounts/profile URLs and voice cards.
- LinkedIn developer application and individual member posting authorization.
- Approved content and calendar. No API key belongs in chat or Git.

The app's internal quotas are conservative policy, not a claim about LinkedIn's
published safe limits. Commercial direct messages need an appropriate reviewed
contact basis, identification and a working opt-out; relevance alone is not consent.
