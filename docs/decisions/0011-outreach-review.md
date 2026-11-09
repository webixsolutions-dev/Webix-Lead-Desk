# ADR 0011: Provider-neutral safety with separate message purpose

Status: implemented in v0.11.0. Supersedes the Titan-only and one-follow-up parts of ADR 0005.

Each mailbox stores its validated connection settings. Its provider selects conservative
readiness and capacity defaults; changing provider does not reroute a locked conversation.
No global mail host overrides control production transmissions. Credentials remain in the
existing encrypted store. Uncertain outcomes remain one stable Message-ID with no automatic
resend; SPECIAL-USE Sent evidence is required for reconciliation.

Draft.kind separates first_touch, follow_up, concept and reply. Draft.sequence_step is only
the position within a campaign follow-up sequence. The existing Draft.step/SendIntent.step
pair remains a unique delivery slot. New follow-ups allocate an unused slot, preventing a
concept or reply from replacing the same sequence position. Existing snapshots and approval
digests are retained. New exact snapshots include the purpose and sequence position.

Sequence offsets are measured from accepted first delivery; each later step also waits at
least two days after its accepted predecessor. Campaign edits are checked again at dispatch.
Every message is approved separately. Any reply or owner pause cancels remaining outreach;
delays hold the lead for explicit review without inflating bounces. Clearing a delay-only hold
never queues a send or overrides a recipient reply.

Automatic address routing is separate from owner-reviewed provenance and contact permission.
DNS establishes routing only, with a 30-day address-bound cache and a fresh transport check.
No SMTP probes, model-generated consent, or inferred mailbox existence are permitted.

Audit Batch calls release worker leases while polling, preserve submission uncertainty and
settle successful partial rows. Drafting remains synchronous. Independent model rates and
conservative reservations protect the app allowance, while provider billing controls remain
owner-configured. Runtime deployment is unchanged; this release creates no hosting resources.
