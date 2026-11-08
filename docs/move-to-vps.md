# Move to VPS — planned Phase 10 procedure

This is an architecture checklist, not an implemented migration tool. Phase 1 does not provision hosting or expose the local application publicly.

1. Approve the hosting environment, cost, HTTPS domain and backup location.
2. Pause all campaigns and stop every scheduler/worker on both environments.
3. Reconcile any uncertain outbound messages before taking the cutover snapshot.
4. Back up the database, private files and configuration. Keep encryption keys separately protected.
5. Create the PostgreSQL schema through the same Alembic migration history.
6. Import through a reviewed migration script preserving IDs, timestamps, approvals, sender locks, suppression, job/send states and inbox cursors.
7. Validate row counts, foreign keys, permissions, message identities and suppression checks. Run acceptance with real sending disabled.
8. Configure APP_MODE=VPS, the PostgreSQL DATABASE_URL, secure cookies, explicit allowed hosts, restricted server secrets, HTTPS/Nginx and separate web/worker systemd services.
9. Verify the new site and backups, then activate exactly one worker. Keep the old PC worker stopped.
10. Before rollback, reconcile any new outbound events. Restoring an outdated snapshot could duplicate sends, so never resume the old worker from stale history.

The migration script, systemd units and full cutover verification are deferred until Phase 10 is explicitly approved. PostgreSQL schema compatibility is tested earlier in CI.

