# Reviewed LinkedIn member posting

## Owner setup

1. Create a LinkedIn developer app with an associated company page; accept the terms
   personally. The company page is for registration; this release posts as members only.
2. Request Sign In with LinkedIn using OpenID Connect and Share on LinkedIn products.
   Required scopes: `openid profile w_member_social`. No passwords/cookies, read-social
   or organization-posting permissions are collected.
3. Set `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `ENCRYPTION_KEY`, secure cookies,
   `PUBLIC_BASE_URL` and `LINKEDIN_POSTING_ENABLED=true` privately on dashboard and worker.
   Register exactly `PUBLIC_BASE_URL/linkedin/oauth/callback`. Keep API version current;
   the tested contract uses `LINKEDIN_API_VERSION=202610`.
4. Each real member opens their own Post calendar, Connect, then the official authorization
   link. On return click Continue securely in the same browser. This same-origin landing
   preserves Strict session cookies. State is single-use, session/member-bound and expires
   after ten minutes. Confirm the displayed member identity matches the assigned profile.
5. Owner saves a fresh post revision after connection and approves the exact caption,
   asset hash, author and scheduled day. Only then can the worker publish. A pre-connection
   approval never silently gains permission to publish as a newly connected account.

## Calendar and content

Weekly planning creates two static-image slots per member; warm-up is two/week for
14 days, then up to four/week, always one/day. Owner can add text or video manually.
All slots are Calgary weekdays 09:00–17:00. Member schedules are at least 30 minutes
apart, with different same-day topics. Existing draft slots reserve capacity too.
Lowered limits are rechecked before execution. Missed days require a new reviewed slot;
the system never backdates or dumps missed posts on the next morning.

Static prompts are free text templates using the approved voice card, no API calls.
They ask for missing facts rather than invent results. Videos receive no prompt or
research: upload an MP4 and provide a caption. Exact duplicate normalized captions
across posts are rejected; maximum 3000 characters, three hashtags, no links/HTML.
The owner reviews factual claims and rights to uploaded content before approval.

## Media and storage

PNG/JPEG: fewer than 36,152,320 pixels. MP4: 75 KB minimum, 3 seconds–30 minutes,
standard ftyp/moov/mvhd container. App maximum is 25 MB per asset, 45 MB raw across
all active uploads; base64 storage adds about one third. Each upload/download request
is at most 1 MB, below Vercel body limits. Files live in the shared database, never
public URLs or ephemeral serverless disk. SHA-256 is checked after assembly and before
provider upload; native download checks it again in the browser. Container validation
is not malware scanning or full codec decoding; LinkedIn performs final media validation.
No uploaded content is executed. No external storage account or paid service is needed.

Remove unused uploads from a post's asset section. An owner may archive bytes after
all referencing posts are published and at least 30 days old; metadata/hashes remain.
Keep enough database and encrypted-backup capacity for media plus normal records.

## Publication and failure handling

The worker commits uploading/submitting before calling the official wrapper. Images
wait at least two minutes after upload; minimal member scope cannot GET versioned
Images API status, so no unsupported permission or legacy workaround is attempted.
Video multipart ranges and ETags are checked; Videos status must be AVAILABLE before
submission. Processing/rejection or expired authorization offers native fallback.
Tokens warn seven days before the provider-returned expiry; no ungranted refresh
capability is assumed. Read-social/analytics permission is not assumed.

A timeout, unknown POST result or interrupted submission becomes needs-check, never an
automatic retry. The real member checks LinkedIn and records the actual post URL or
confirms not published. Native copy/download is released after a member-only claim
on the approved scheduled day. Changes after a claim are blocked. Backup maintenance
also holds in-flight work. Work outside this app cannot be observed automatically.

## Verification

Offline tests cover exact approvals, member isolation, caps, idempotency, uncertain
results, state-before-HTTP, expiry, OAuth state/session binding, encrypted token removal,
media integrity/container limits, actual HTTP upload/CSRF paths and official payloads.
No real OAuth grant, media upload or post has been made. Run one explicitly approved
real-member acceptance post only after owner setup; verify the returned post URL and
native fallback on the actual host. Browser screenshot checks use synthetic data only.

Primary references:
- https://learn.microsoft.com/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2
- https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api
- https://learn.microsoft.com/linkedin/marketing/community-management/shares/images-api
- https://learn.microsoft.com/linkedin/marketing/community-management/shares/videos-api
