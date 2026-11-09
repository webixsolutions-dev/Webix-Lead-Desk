# Per-mailbox HTML signatures

Signatures belong to the mailbox and are editable only by an active owner through a CSRF-protected
form. Each signature is a versioned `settings` entry (`mailbox_signature:<mailbox UUID>`); the existing
table, backups and RLS cover this non-secret configuration without a schema migration. Customer
signature content is stored in the private database, not in source control.

`sending/signature_html.py` uses nh3's HTML5 sanitizer and a restricted tinycss2 declaration/token
allowlist. Pasted script, form, SVG, iframe, editor IDs/tracking attributes, relative/internal URLs
and CSS network fetches are removed. Public HTTPS image references are preserved without a backend
fetch. Inline RGB gradients remain stable across sanitation; solid backgrounds are recommended as
an email-client fallback. The saved HTML generates its own readable text alternative.

`sending/signatures.py` owns optimistic-version saves. `api/signatures.py` exposes editing and
authenticated previews; preview documents have a separate script-free sandbox CSP, no referrer and
no caching. The normal dashboard keeps its existing CSP. Preview frames have no sandbox permissions.
Signatures are never inserted as trusted markup into a normal dashboard document.

Draft and test snapshots include the exact signature HTML, text and version. Current signature
changes block approval/delivery until a new revision is reviewed, including the final SMTP DATA
check. Tests with HTML use approval format 2 and require the worker's `email_signatures=1` capability;
old workers cannot validate that format. Legacy unsigned-content changes are never silently added.

The worker produces multipart/alternative mail, keeping a full text alternative and the mandatory
outreach identity/unsubscribe footer. Signature links are separate from campaign copy; existing
first-touch subject/body link and pricing checks stay in force. Sent-folder reconciliation compares
the HTML alternative as well as the existing headers and plain text. Updating a signature never
queues, approves or sends an email. Older workers must be updated before using HTML signatures.
