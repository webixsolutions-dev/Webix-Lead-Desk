# Owner setup before the first live pilot

The application code covers phases 1–10. CI and synthetic tests are separate from a working hosted account or live provider acceptance. No prospect was contacted and no client outcome is implied.

## 1. Choose the initial runtime

**No provider purchase:** use PC mode and SQLite with the Windows commands in the README. Keep the PC awake for background work. The dashboard is local to that PC.

**Hosted dashboard:** use the existing Vercel project plus a dedicated PostgreSQL database and a PC worker. Neon is supported but not connected. Select an eligible zero-cost database/account, or explicitly authorize the paid organization's cost before provisioning. Do not reuse an unrelated database. Vercel currently has `APP_MODE=VERCEL`; the remaining production database/secrets and owner account are not configured. A green build is not a verified live dashboard.

Set the private variables listed in [Vercel setup](deploy-to-vercel.md), migrate explicitly, create the owner via the local hidden-password prompt, then verify sign-in and a worker health job. Never paste passwords or keys into chat. If moving existing SQLite data, follow [cutover](move-to-vps.md); do not create accounts in the empty target before restoration.

## 2. Configure the real sender

Use Settings to create the Camzio Tech Titan profile for `sales@camziotech.com` with the actual sender name, business mailing address and contact details. Store its Titan password only in the private credential field. Web and worker must share the original `ENCRYPTION_KEY`. A configured mailbox is not evidence of successful provider access.

Verify the actual domain's SPF, DKIM and DMARC, privacy/identification details and provider sending limits. Confirm the specific test recipient and exact test content before any real send. Record actual test acceptance only after it happens. Keep `LIVE_SEND_ENABLED=false` on Vercel permanently; enable it only on the separate worker after these checks and approval. The application also requires current contact-basis evidence, suppression clearance, exact-message approval, a fresh inbox and the pilot gate.

A separate Webix partner profile uses its own approved business identity and Hostinger business mailbox. Do not invent this address or reuse a different brand's sender identity.

## 3. Review targeting, offers and writing guidance

Campaigns can change industry, city, offered services and supported country (Canada/US). Prices and proof claims are editable and are never invented. Start with the reviewed Calgary HVAC/roofing/cleaning pilot; confirm the actual ten businesses and their website/contact evidence. The pilot allows at most five first touches before acceptance. Later daily capacity ramps toward 50 only within mailbox limits and the Calgary sending window.

Write initial Playbook guidance and approved examples in the dashboard. Suggestions from your edits and outcomes stay pending until you apply them. Memory is versioned application guidance; the system does not train the model or adopt website/inbox instructions.

Every message revision needs owner approval. Up to four follow-ups are supported, each with separate exact approval and campaign offsets 3/7/12/20 from first acceptance (partner 4/10), with at least two days after the previous actual acceptance. Prepare each step after the previous delivery; any prospect reply cancels pending outreach. Delivery-delay notices hold the lead without increasing bounces. Review the sender mailbox and clear the delay hold in Replies; scheduling still requires owner action. Any other reply prevents that delay-only release. Interested replies can become assigned call cards and separately approved concept drafts. Configure the real `PUBLIC_BASE_URL` before publishing concept links. Do not claim invented booking availability, revenue, ad spend or client results.

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

## v0.11 provider and AI setup

1. Create Titan for Camzio, Hostinger for the Webix Solutions partner lane, or Gmail for a
   second client sender. Partner senders need a business domain and a real named person;
   personal Gmail is not allowed. Credentials remain encrypted and write-only. Google app
   passwords require eligible 2-Step Verification; OAuth onboarding is not implemented.
2. Each mailbox has provider-correct SMTP/IMAP settings. SMTP supports SSL 465 or STARTTLS
   587; IMAP uses SSL 993. Hostnames are allowlisted to the selected provider, with public
   address checks before connecting. Sent reconciliation uses advertised SPECIAL-USE
   `\Sent`, including localized folder names; absent flags fail closed and need manual review.
3. Confirm limits in your provider account. Titan defaults 50/hour, 300/day, published bounce
   ceilings 5/hour and 10/day; the app pauses earlier at three daily bounces. Hostinger
   defaults 30/hour and 300 rolling-24-hour messages: set the actual plan (trial 100, legacy/free
   300, Business Starter 1,000, Business Premium 3,000). Gmail defaults 10/hour and 20/day.
   The 10/20/35/50 ramp, campaign limits and Calgary window still cap these ceilings.
4. Titan/Hostinger require SPF, DKIM, DMARC, privacy/identity, limits and actual test acceptance.
   Personal gmail.com authentication is managed by Google; confirm App Password, privacy,
   limits and test acceptance. Nothing here proves real delivery until owner-controlled testing.
5. Address checks run automatically before approval and refresh after 30 days or an address
   change. Null MX, missing routing and temporary DNS errors block approval. `info@` and
   `sales@` are allowed; other known role inboxes need owner allowance; no-reply is blocked.
   Provenance/permission evidence stays separate. No SMTP probes are made. The transport
   also rechecks DNS immediately before an attempt.
6. The owner has reported claiming Pro API credits into the Personal API organization.
   Verify that organization in the API key/project, current balance and expiry. Configure
   provider-side spend controls (verify whether they are enforceable caps or alert budgets)
   and turn off automatic recharge. The app's verified allowance is a second enforced cap;
   no unverified amount is automatically entered.
7. Use `OPENAI_AUDIT_MODEL=gpt-6-luna` and `OPENAI_DRAFT_MODEL=gpt-6.1-sol` after checking
   account access. Enter separate current standard input/output rates; zero values block AI.
   Legacy `OPENAI_MODEL` settings remain accepted for older installs; migrate them to the
   two explicit settings. AI stays disabled by default and the copyable brief remains available.
8. `OPENAI_PROCESSING=batch` selects overnight audits only. Drafting stays synchronous.
   Each audit job submits one request, stores its batch ID, releases the worker lease and
   polls every five minutes, bounded to 25 hours. Partial completed rows from expired or
   cancelled batches can settle; failed/missing rows fail closed. Uncertain creates never
   automatically resubmit. Reservations and settlement use conservative standard rates,
   not claimed provider-invoice discounts. This is not a multi-lead batch coalescer.
9. Apply migrations through `0012_outreach_spec` to the shared web/worker database before
   restarting the updated worker. Back up first and stop the old worker. Historical exact
   approvals are not rewritten. Partner guidance is seeded once and remains owner-versioned.
10. Fill Local team members with real names and city/country. Empty city records country-wide
    availability only when that is true. Partner drafts may promise an in-person meeting only
    with a matching named member; otherwise use video and the review reminder. No representative
    is invented. Verify Proof Library facts before approving any draft.

Sources checked 2026-11-09: [API credits](https://developers.openai.com/api/docs/guides/chatgpt-api-credits),
[Batch API](https://developers.openai.com/api/docs/guides/batch),
[Google app passwords](https://support.google.com/mail/answer/185833),
[Hostinger settings](https://www.hostinger.com/support/1575756-how-to-get-email-account-configuration-details-for-hostinger-email/).


## v0.12 discovery and call setup

Follow [the acquisition guide](acquisition.md). Apply migrations 0013/0014 after
backup, configure search price/caps, then enable the existing AI switch only after
funding is verified. `OPENAI_SEARCH_MAX_OUTPUT_TOKENS` defaults to 6000. Select the
no-website package from Offer Library. No Google Places account is needed while
that provider stays off. Start the existing PC/VPS worker using the shared database.
Reviewers can use Today → Calls; Settings is now owner-only. No live sender or
production Neon acceptance is implied by the offline test suite.

LinkedIn public posting is optional and off until each real member authorizes the
owner-configured developer app. Follow [the posting setup](linkedin-posting.md);
enter all credentials privately, never in chat. Approve one real acceptance post
only after reviewing the caption and the exact uploaded asset.
