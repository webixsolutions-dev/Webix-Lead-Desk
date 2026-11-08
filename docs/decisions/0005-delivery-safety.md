# Delivery, replies and uncertain outcomes

One business has one conversation. The sender/contact/campaign can change only before the
first dispatch reservation. Every revision has an exact owner approval; dispatch rechecks
that digest, sender footer, contact basis, suppression, Calgary window, limits and inbox freshness.

A durable send intent is unique per conversation/step. It receives a stable Message-ID before
SMTP. Intent states distinguish scheduled, transmitting, accepted, uncertain, held
and cancelled. Known pre-acceptance transient failures may retry with backoff. A disconnect or
timeout after DATA begins is uncertain and never automatically resent. A crashed reserved or
transmitting job is held. Reconciliation requires a matching provider Sent record; its absence
is not proof of non-delivery. Thread references and sender identity survive restart/migration.

The singleton worker and individual job use renewable leases with claim ownership. Dispatch
also reserves daily capacity and the mailbox's next-send time transactionally. Unknown sends
retain capacity. Messages are spaced 3–8 minutes; there are no catch-up bursts. Limits ramp
10/20/35/50 per day, include follow-ups, and can only be lowered by the owner. Calgary sends
occur Monday–Friday, 08:30–11:30 America/Edmonton. Three distinct bounces pause the mailbox for
the rest of that local day. The initial pilot allows only one approved follow-up per thread.

IMAP sync completes before each dispatch. UIDVALIDITY/UID checkpoints and message deduplication
prevent repeat processing. Any correlated reply stops pending follow-ups before classification.
Unsubscribe/not-interested replies add global suppression. Ambiguous and automatic replies stay
held for human review. Incoming content never receives tools, changes guidance or authorizes
sending. The UI renders escaped plain text and never loads remote email images or attachments.

Mailbox/API credentials are encrypted with a dedicated environment-held Fernet key. They are
write-only in the dashboard and never logged. SMTP/IMAP endpoints are allowlisted Titan hosts
with verified TLS. Live sending defaults off and requires owner-confirmed sender readiness and
provider test acceptance; fake tests do not count as live acceptance. No real test email is sent
without owner-approved test recipients and exact content.
