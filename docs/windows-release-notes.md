# Webix Lead Desk v0.30.0

Connect your own native WhatsApp agent for private owner alerts and exact-version email approvals.

1. Stop the Windows companion, install this release once, then Start Worker. Existing encrypted desktop settings stay with the same Windows account.
2. In the dashboard, open Settings → Sending → WhatsApp Agent. Paste the API key from WhatsApp → Settings → Agents. Keys are encrypted and never shown again.
3. Choose summary-only alerts or explicitly share business/email details to enable WhatsApp approvals. Save, then send the displayed LINK code to your agent within 15 minutes.

Full-detail alerts show the business, website, evidence excerpt, exact email text and a complete dashboard review link. After reviewing the full message, signature and attachments, send APPROVE followed by its code to approve and schedule that version. REJECT holds the unsent draft; EDIT opens the dashboard editor/rewrite flow. Codes expire after 24 hours and stale revisions cannot bypass current checks.

New incoming emails trigger alerts while the worker runs. Every reply still requires owner approval. This integration does not enable live email sending, paid AI or LinkedIn automation. WhatsApp agent chats are not end-to-end encrypted; summary-only sharing is the default. Reactions, voice notes, bulk approvals and free-form WhatsApp rewrites are not approval commands.

Migration 0026 adds private agent pairing, notification and replay records. Backups omit agent pairing sessions, so reconnect after restoring a backup. API-key and normal dashboard settings changes do not require reinstalling; future worker-code/schema updates may require another worker update.

The existing Command Center, meeting/proposal tools and optional owner authenticator remain available. Live WhatsApp delivery must be tested after the owner supplies their own key and completes pairing.
