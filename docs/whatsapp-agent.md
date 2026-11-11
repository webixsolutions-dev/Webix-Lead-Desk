# WhatsApp owner agent

This integration uses the native **WhatsApp → Settings → Agents** platform. It is not a
WhatsApp Business Cloud API integration, does not read other chats, and does not contact
prospects through WhatsApp.

## Connect

1. Install Windows worker v0.30 or newer, retain the saved database/encryption configuration,
   and start it. VPS users update code/migrations and restart the worker.
2. Create an agent in WhatsApp, name it Webix Lead Desk, and copy its API key.
3. Open **Settings → Sending → WhatsApp Agent** in Lead Desk. Paste the key privately.
4. Choose summary-only alerts, or explicitly share business/email details to enable remote
   email approvals. Confirm the agent is yours and save.
5. Send the displayed `LINK <code>` to that agent within 15 minutes. Refresh the dashboard
   to check connection status. Saving a key again revokes the prior pairing and review codes.

The worker needs `PUBLIC_BASE_URL` pointing at the authenticated dashboard. The Windows app
sets this from its dashboard address. No webhook, additional AI allowance or phone-number
field is required. The opaque recipient ID comes from the verified pairing message.

## Notifications and approvals

The worker polls at most once per 20 seconds when available, queues each pending email
revision once per pairing, and alerts on emails received after pairing. One notice is sent
per poll, with incoming-reply/command notices before draft notices. A busy/offline worker
will delay alerts. No transport runs inside Vercel HTTP requests.

Full-detail notices include the business, website, a bounded evidence excerpt, sender,
recipient, subject and exact plain-text message including signature text/footer. The
review link shows the complete research, rendered signature and attachments. Review
that full message before entering its explicit `APPROVE <code>` command. That action
approves and schedules exactly the bound digest through the existing email services.
Live sending still depends on worker controls, limits, suppression and contact checks.

`REJECT <code>` rejects the unsent revision. `EDIT <code>` returns the dashboard editor,
including its existing rewrite and owner price controls. Free-form WhatsApp rewrites,
voice commands, reactions and bulk approvals are not supported. “Yes” does nothing.

Codes expire after 24 hours. Edited/superseded content, new replies, changed signatures,
suppression or other current eligibility failures cannot bypass existing review policy.
Conversation warnings requiring a written override must be handled in the dashboard.
Every reply still requires its own owner approval, regardless of cold-outreach delegation.
Summary-only or oversized previews have no actionable remote approval code.

A successful send records WhatsApp's message ID, not proof of delivery or reading. A
connection reset, timeout, error or interrupted in-flight notice is not automatically
resent. Review the notification status and handle the draft in the dashboard. A command
interrupted after being claimed also requires dashboard review; it cannot run twice.

## Privacy and recovery

The key is encrypted using the existing `ENCRYPTION_KEY` vault. API keys and provider
error bodies are never rendered or logged. Pairing codes are hashed; incoming command
IDs are hashed and raw owner chat content is not retained. Notice bodies expire from
storage seven days after their review expiry; command replay records remain for 31 days.
Agent chats are **not end-to-end encrypted**. Summary-only mode is the default.

Disconnect deletes the key and revokes pending codes locally. Remove/regenerate the key
in WhatsApp as well if access should be revoked at the provider. Backups omit pairing,
notice and command tables; restoring requires a new pairing, never a transferred
approval session. Other encrypted credentials retain the existing backup behavior.

## Verified contract

[Official WhatsApp Agent Platform developer manual, version 1, August 25 2026](https://www.whatsapp.com/developer/WhatsApp-Agent-Platform-Developer-Manual.pdf):

- Fixed origin `https://api.whatsapp.com/agent/v1`, bearer token, redirects rejected.
- `GET /updates`: persisted `next_offset`; `offset=0` during pairing; bounded batches.
- `POST /messages`: text only, max 4096 characters, no URL previews, opaque `user:` recipient.
- 12 message requests/minute and 15 poll requests/minute; worker pacing stays below both.
- Receive-only reactions are intentionally ignored for authorization.

Offline tests use synthetic payloads and never contact WhatsApp or email prospects. Live
pairing/delivery can only be verified after the owner adds their own key in the dashboard.
