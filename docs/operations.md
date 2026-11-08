# Backup and recovery

Use the same reviewed release for backup and restore. Backup files contain business and account data; treat them as private even though they are encrypted. No backup, export or key belongs in Git. The Operations page shows runtime status; administrative backup and restore commands run only on a trusted PC/server.

## Keys and limits

A new local setup generates separate `SESSION_SECRET`, `ENCRYPTION_KEY` and `BACKUP_KEY` values without displaying them. To add only a missing backup key to an existing installation:

```sh
uv run python scripts/add_backup_key.py --env-file .env
```

Keep a private recovery copy of both encryption keys separately from the backup. `BACKUP_KEY` authenticates/encrypts the file. The original `ENCRYPTION_KEY` is also required to recover stored mailbox/provider credentials. Losing either necessary key makes that data unrecoverable. Do not rotate keys by replacing environment values; credential rotation needs a separately reviewed migration. The backup key need only exist on the trusted administration process, not the Vercel web function.

The default unencrypted snapshot limit is 100 MB. Snapshots are buffered in memory. Larger datasets need a reviewed limit and enough memory, or a separately designed database-native recovery procedure. `verify-backup` authenticates and validates the file but does not prove a successful restore; rehearse restoration periodically.

## Create an offline backup

1. Confirm the private `.env` points to the intended database.
2. Enable the shared maintenance gate:

   ```sh
   uv run lead-desk maintenance on --note "Scheduled private backup"
   ```

3. Stop every web and worker process that uses this database. For Vercel, pause incoming traffic/deployments or otherwise stop application access before claiming the workspace is offline. Maintenance alone is not an offline guarantee: a request already in flight may still finish, and login/logout remain available. Never use `--offline-confirmed` while writers are active.
4. Wait for worker/job leases to expire. Defaults are 30/60 seconds; use the configured durations. Backup refuses live leases. Interrupted running jobs are marked failed; transmitting email becomes uncertain and is never automatically replayed.
5. Create a new file and authenticate it:

   ```sh
   uv run lead-desk backup --file backups/desk-001.wld --offline-confirmed
   uv run lead-desk verify-backup --file backups/desk-001.wld
   ```

The output contains a row count and encrypted-file SHA-256, not records or keys. Existing files are never overwritten. Copy the file to private backup storage, keep its hash and restrict access. Unix files are created with mode 0600; configure suitable account-only NTFS permissions on Windows. The snapshot includes all mapped data except login sessions/rate buckets. Environment values are not exported. Credential ciphertext is verified using the current key before backup.

For an ordinary backup, explicitly resume the same database after verification, then restart its processes:

```sh
uv run lead-desk maintenance off --note "Backup verified; resuming original workspace"
```

Do not resume the old database during a transfer.

## Restore to an empty database

Create a private target profile pointing to an empty database, with the original credential and backup keys, `LIVE_SEND_ENABLED=false`, and the matching application release. Stop all target processes. Do not create an owner account first; the backup preserves accounts and password hashes.

```sh
uv run lead-desk migrate
uv run lead-desk restore --file backups/desk-001.wld --offline-confirmed
uv run lead-desk check
```

Commands use the current `.env`. Confirm its destination before each operation. Restore rejects unrelated tables, existing records, wrong keys, mismatched schemas and corrupted files. It inserts within one transaction, verifies counts and full canonical row content, and rolls back failed inserts/comparisons. The restored workspace stays in maintenance, login sessions are revoked, and sender locks, exact approvals, suppression, reservation history and uncertain-send identities are preserved.

Start the target web process with sending disabled and inspect accounts, leads, drafts, stops, delivery holds and Operations. Run a harmless health job only after explicit maintenance-off. Resume one selected database/worker, never both the source and a restored copy. Enabling maintenance-off is not approval to enable live sending.

## Recovery decisions

- Unknown SMTP outcome: keep the hold. A negative/absent Sent search is not proof that no delivery occurred. Use the existing positive-evidence reconciliation workflow.
- Failed research/health work after an offline snapshot: review it and enqueue fresh work where appropriate; do not manually change job state to bypass claim checks.
- Interrupted restore: retry only against a verified empty target. Never delete a populated database merely to make a command pass.
- Application upgrade: stop work, back up using the old matching release, migrate explicitly, deploy the matching new release, verify, then resume. Do not roll back a populated schema blindly.
- Lost owner access: use the local `reset-password` command; it revokes that user's sessions.

These tools were tested with synthetic data. They do not establish live Titan acceptance, a completed customer pilot or a production disaster-recovery exercise.
