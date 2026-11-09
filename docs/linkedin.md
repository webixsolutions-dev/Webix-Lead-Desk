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

## L2 planner and PC startup

Migration 0016 adds per-profile policy locks, quota reservations, sequence progress,
leased human tasks and person-day reservations. Fresh migrated databases remain empty,
so backup/restore and SQLite-to-PostgreSQL transfer retain their existing checks.
The worker plans every LINKEDIN_PLAN_INTERVAL_SECONDS (default 60) after 07:30 on
Calgary weekdays. Slots are 09:00, 11:30 and 14:30; late startup spreads work through
remaining slots, with five-minute spacing after the last batch and a 17:00 cutoff.
Tasks more than five days overdue at a message step require review. An expired human
action lease becomes needs_check, never a queued replay. Read Today → LinkedIn for
plans; only the owner sees other members' plans. L2 does not release invitations or
messages: L3 supplies exact draft approval and manual action cards.

The first fourteen Calgary calendar days cap invites at 10/day, first messages at
5/day, views at 20/day, comments at 5/day and posts at 2/week. Later hard ceilings
are 20 invites/day, 80/week, 300 pending, 15 first messages/day, 30 total messages/day,
40 views/day, 10 comments/day and 4 posts/week with at most one/day. InMail stays zero.
Settings → LinkedIn profiles lets the owner lower limits. Already reserved slots
remain counted after cancellation; this is intentionally conservative. Replanning
the same task/day never consumes another slot. A new day gets its own allowance;
unused quota never carries forward. A pending invite older than 21 days creates a
human withdrawal task. No LinkedIn API or browser operation is performed by planning.

A named personal email matches a Person through contact_id or exact normalized
personal_email, including contacts imported later. Email and LinkedIn use one durable
person/day fence before SMTP DATA or a human action start. The same uncertain action
keeps its reservation; a second channel cannot reuse it. Generic company inboxes keep
the existing email/form exclusivity. Logging an actual reply still cancels all pending
company outreach, independently of these quotas.

On the actual Windows PC, finish local setup and migration, then run PowerShell as
Administrator: `powershell -NoProfile -File scripts/install-autostart.ps1`. Enter the
Windows task account credential only in Windows' credential prompt. The script creates
two AtStartup tasks, runs while signed out, retries process failure every minute, starts
missed tasks when available and rejects overlapping instances. Task Scheduler stores
its logon credential; no password is written into the repo or task command line. The
worker singleton lease also prevents two active workers. `-ValidateOnly` checks script
syntax without creating tasks; `-Uninstall` stops and removes only these two named tasks.

PC_KEEP_AWAKE=true keeps the Windows worker thread awake on Calgary weekdays 07:30–17:00,
then releases its request. It does not change the user's power plan or keep the display
on. Shutdown, lid policies and a stopped worker still stop work; reconnect and inspect
needs_check items rather than assuming an action completed. Set false to disable it.
VPS supervision continues to use the existing systemd services; Vercel has no worker.

Windows references: https://learn.microsoft.com/powershell/module/scheduledtasks/new-scheduledtasksettingsset
and https://learn.microsoft.com/powershell/module/scheduledtasks/new-scheduledtaskprincipal

All time calculations and displays now use the locked Python tzdata package rather
than the host OS database. Alberta adopted permanent UTC-6 beginning November 2026;
stale Linux/Windows data could otherwise shift every window by an hour. The app keeps
America/Edmonton as its IANA identity and preserves historical seasonal rules. Before
upgrading to newer tzdata, run the historical/current boundary regressions.
Source: https://www.alberta.ca/albertas-new-time-system-abt
