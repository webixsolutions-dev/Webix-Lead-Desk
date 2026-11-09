# Owner setup before the first live pilot

The application code covers phases 1–10. CI and synthetic tests are separate from a working hosted account or live provider acceptance. No prospect was contacted and no client outcome is implied.

## 1. Choose the initial runtime

**No provider purchase:** use PC mode and SQLite with the Windows commands in the README. Keep the PC awake for background work. The dashboard is local to that PC.

**Hosted dashboard:** use the existing Vercel project plus a dedicated PostgreSQL database and a PC worker. Neon is supported but not connected. Select an eligible zero-cost database/account, or explicitly authorize the paid organization's cost before provisioning. Do not reuse an unrelated database. Vercel currently has `APP_MODE=VERCEL`; the remaining production database/secrets and owner account are not configured. A green build is not a verified live dashboard.

Set the private variables listed in [Vercel setup](deploy-to-vercel.md), migrate explicitly, create the owner via the local hidden-password prompt, then verify sign-in and a worker health job. Never paste passwords or keys into chat. If moving existing SQLite data, follow [cutover](move-to-vps.md); do not create accounts in the empty target before restoration.

## 2. Configure the real sender

Use Settings to create the Camzio Tech Titan profile for `sales@camziotech.com` with the actual sender name, business mailing address and contact details. Store its Titan password only in the private credential field. Web and worker must share the original `ENCRYPTION_KEY`. A configured mailbox is not evidence of successful provider access.

Verify the actual domain's SPF, DKIM and DMARC, privacy/identification details and provider sending limits. Confirm the specific test recipient and exact test content before any real send. Record actual test acceptance only after it happens. Keep `LIVE_SEND_ENABLED=false` on Vercel permanently; enable it only on the separate worker after these checks and approval. The application also requires current contact-basis evidence, suppression clearance, exact-message approval, a fresh inbox and the pilot gate.

A separate Webix partner profile uses its own approved business identity and Titan mailbox. Do not invent this address or reuse a different brand's sender identity.

## 3. Review targeting, offers and writing guidance

Campaigns can change industry, city, offered services and supported country (Canada/US). Prices and proof claims are editable and are never invented. Start with the reviewed Calgary HVAC/roofing/cleaning pilot; confirm the actual ten businesses and their website/contact evidence. The pilot allows at most five first touches before acceptance. Later daily capacity ramps toward 50 only within mailbox limits and the Calgary sending window.

Write initial Playbook guidance and approved examples in the dashboard. Suggestions from your edits and outcomes stay pending until you apply them. Memory is versioned application guidance; the system does not train the model or adopt website/inbox instructions.

Every message revision needs owner approval. Up to four follow-ups are supported, each with separate exact approval and at least three days after the previous accepted message. Prepare each step after the previous delivery; any prospect reply cancels pending outreach. Delivery-delay notices are operational events, not prospect replies. Interested replies can become assigned call cards and separately approved concept drafts. Configure the real `PUBLIC_BASE_URL` before publishing concept links. Do not claim invented booking availability, revenue, ad spend or client results.

## 4. Optional integrations and current limits

| Capability | Current implementation |
|---|---|
| Lead discovery | CSV/manual source review and Open Calgary worker import |
| Google Maps | Manual review/CSV; automatic Maps collection is not implemented |
| Website audit | Bounded public-page fetching and evidence extraction; owner visual review |
| Social/Meta ads | Owner-recorded observations; no automated login or ad-spend inference |
| Personalized drafts | Manual text or copyable ChatGPT brief; optional paid OpenAI API after verified funding/rates |
| ChatGPT Pro | Eligible subscriptions can claim API credits; verify the linked organization, remaining balance and expiry |
| Firecrawl/Places | Private key storage is available for later adapters; adapters are not enabled or implemented |
| Contact forms | Approved copy and manual submission/outcome recording; no automatic external filling/submission |
| Demos | Reviewed concept pages/flows tied to evidence; no full prospect website built automatically |
| Learning | Owner-approved guidance versions and restoration; no automatic model training |

No provider account purchase is automatic. Start with the paths that need no new API spending. A one-week client close is a sales goal, not a guaranteed system result.

## 5. Record real acceptance

Verify the actual PC worker, hosted sign-in if used, Titan inbox sync, approved test delivery/threading, reply cancellation and suppression against owner-controlled test addresses. Then review the pilot cohort and approve individual outreach. Synthetic CI results do not replace these checks. Keep an encrypted backup and rehearse restoration before relying on the workspace as the only record of outreach.

## Review fixes and provider setup

- Select Titan or Gmail/Google Workspace when creating a sender. Gmail uses TLS SMTP 465 and IMAP 993 with a Google app password where eligible (2-Step Verification required). OAuth onboarding is not implemented. Credentials go only into the private Settings field. Real provider acceptance remains untested. Gmail Sent reconciliation currently expects `[Gmail]/Sent Mail`; other localized folder names require a configuration improvement.
- DNS MX/null-MX and implicit A/AAAA routing are checked before each SMTP attempt. This is not mailbox-existence verification and does not replace address provenance or contact permission. DNS outages postpone delivery; domains without routing are held.
- All message bodies/subjects omit prices and guarantees; first-touch bodies/subjects also omit links. Identity/contact footers are preserved.
- `OPENAI_AUDIT_MODEL` and `OPENAI_DRAFT_MODEL` select independent models, with independent verified standard-token rates. Suggested roles: Luna for assessment, Sol for drafting. API model access must be confirmed on your account.
- `OPENAI_PROCESSING=batch` selects optional asynchronous Batch API processing for assessment and drafting. Each job submits one request, persists its provider batch ID and polls every five minutes for up to 25 hours. This is not a multi-lead batch coalescer. Uncertain creates never resubmit automatically. Reserves and usage accounting remain conservative at configured standard rates (they do not claim to reproduce provider invoices or discounts). Input files contain business evidence; review OpenAI retention settings before enabling.
- Apply migration `0011_ai_batch` to web and worker's shared database before starting the updated worker. Stop the old worker first.
- AI and live sending remain disabled until private credentials, verified allowance/expiry and sender acceptance are configured. A user-reported claimed balance is not silently written as a verified live balance.

Sources checked 2026-11-09: [API credits](https://developers.openai.com/api/docs/guides/chatgpt-api-credits), [Batch API](https://developers.openai.com/api/docs/guides/batch), [Google app passwords](https://support.google.com/mail/answer/185833).
