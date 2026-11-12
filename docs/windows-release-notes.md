# Webix Lead Desk v0.30.7

This release supports campaign email signatures for shared business mailboxes. Keep the existing dashboard and local account. Stop the worker, install v0.30.7, then select Start Worker. This companion runs approved mailbox tests and the existing research functions.

The WhatsApp idle polling fix from v0.30.2 is included. If you choose to upgrade the worker, stop it first and start it after installing. Your existing pairing and key are retained.

Connect your own native WhatsApp agent for private owner alerts and exact-version email approvals.

1. Stop the Windows companion, install this release once, then Start Worker. Existing encrypted desktop settings stay with the same Windows account.
2. In the dashboard, open Settings → Sending → WhatsApp Agent. Paste the API key from WhatsApp → Settings → Agents. Keys are encrypted and never shown again.
3. Choose summary-only alerts or explicitly share business/email details to enable WhatsApp approvals. Save, then send the displayed LINK code to your agent within 15 minutes.

Full-detail alerts show the business, website, evidence excerpt, exact email text and a complete dashboard review link. After reviewing the full message, signature and attachments, react to that notification: 👍 approves and schedules the exact version, 👎 rejects and holds it, and ✏️ opens the dashboard editor/rewrite flow. Typed APPROVE/REJECT/EDIT plus the displayed code remains available as a fallback. Actions expire after 24 hours and stale revisions cannot bypass current checks.

New incoming emails trigger alerts while the worker runs. Every reply still requires owner approval. This integration does not enable live email sending, paid AI or LinkedIn automation. WhatsApp agent chats are not end-to-end encrypted; summary-only sharing is the default. Only the three documented reactions on the exact actionable notification are commands; other reactions, voice notes, bulk approvals and free-form WhatsApp rewrites are not approval commands.

Migration 0026 adds private agent pairing, notification and replay records. Backups omit agent pairing sessions, so reconnect after restoring a backup. API-key and normal dashboard settings changes do not require reinstalling; future worker-code/schema updates may require another worker update.

The existing Command Center, meeting/proposal tools and optional owner authenticator remain available. Live WhatsApp delivery must be tested after the owner supplies their own key and completes pairing.
