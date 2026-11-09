# Fixed mailbox connection tests

The owner's SMTP acceptance check cannot depend on already accepted outreach readiness.
The dashboard therefore previews one fixed diagnostic email, takes explicit approval for one
recipient and queues it for the PC/VPS worker. This is a separate permission from live outreach.

The existing jobs and audit tables are sufficient: no new schema or prospect records are created.
A Fernet-authenticated approval binds the request UUID, actor, sender identity, provider endpoints,
recipient, fixed subject/body and approval time using the shared encryption key. It expires after
24 hours. The worker verifies it, the active owner and its queue lease before SMTP DATA.

A unique job ID deduplicates repeated submissions. Three database-enforced slot keys per mailbox
per UTC day bound diagnostic volume. A durable transmission marker is written before SMTP; retries,
expired claims and unexpected failures never replay a diagnostic. Ambiguous outcomes retain their
marker and block a new test to that inbox until an owner records actual receipt. The receipt is
audited separately and does not enable readiness or outreach.

Old workers do not advertise this capability, so the form remains disabled until an updated worker
is online. Vercel only queues approved requests and rejects diagnostic execution configuration.
The Windows companion enables the narrowly scoped diagnostic capability while keeping outreach,
AI providers and LinkedIn posting off. Test mail bypasses campaign windows because it is an explicit
owner setup action; it cannot carry arbitrary marketing copy or generate follow-ups.

SMTP acceptance and inbox delivery are separate results. The transport does not guarantee that a
Sent-folder copy exists. The owner checks the actual recipient inbox before confirming receipt.
