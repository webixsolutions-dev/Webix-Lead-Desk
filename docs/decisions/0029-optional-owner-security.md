# Optional owner authenticator and device sessions

The owner may choose a second sign-in factor in Settings → System → Account security. It starts off, is available only to owners, and never becomes a sending or worker prerequisite. Reviewers cannot read or change security settings. Only the signed-in owner's own devices appear.

Enrollment requires the current password and a current device, encrypts a random 160-bit secret with the workspace ENCRYPTION_KEY, and expires after ten minutes. The owner must verify a six-digit code before it is enabled. Setup does not expose stored secrets on GET requests. Enabling it invalidates other sessions and unfinished sign-ins.

Password verification for an enrolled owner creates only a five-minute challenge, bound to its cookie, CSRF token, password version and factor version. A full session is issued only in the same transaction that consumes the challenge and a fresh authenticator counter or unused recovery code. Account row locks serialize these operations on SQLite and PostgreSQL. Codes allow one adjacent 30-second step and cannot be reused. Both password and code attempts are rate limited. Password reset revokes sessions and unfinished challenges but preserves MFA.

Eight random 128-bit recovery codes are shown once, stored as hashes and usable once each. Changing factors, replacing codes or signing out other devices requires current-password verification and an existing factor when enabled. Changes revoke other sessions and unfinished challenges while preserving the current device. Device descriptions are untrusted browser labels, not hardware identity; session tokens are never displayed. No IP addresses are stored by this feature.

Keep recovery codes in a private password manager and retain the existing encryption key. Losing the encryption key affects stored integration credentials as well as authenticator secrets. Restore the original key through the private deployment configuration; never paste it into chat. No email-based MFA removal or session-only reset bypass is added.

Migration 0025 adds three private tables with PostgreSQL default-deny RLS, plus browser and last-seen fields on sessions. It does not enroll any account. Backup exports retain the encrypted active factor and recovery-code state, require the same encryption key, and omit sessions, unfinished sign-in challenges and pending enrollment. An exported backup remains sensitive. A website deployment does not update an already installed Windows executable: install the matching release once and restart the worker against the upgraded schema.

References: [RFC 6238](https://www.rfc-editor.org/rfc/rfc6238) and [OWASP MFA guidance](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).

Repository visibility remains the owner's separate choice. Before making it private, confirm the Vercel and CI GitHub applications retain repository access. This implementation does not change GitHub permissions or claim that repository visibility was changed.
