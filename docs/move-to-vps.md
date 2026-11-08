# Move the workspace without replaying outreach

The primary hosted design remains **Vercel web + PostgreSQL + PC worker**. A VPS is optional. No server is provisioned by changing `APP_MODE`, and no provider purchase is required by this release. PC-only SQLite remains supported.

## Choose the destination

| Move | Data operation |
|---|---|
| SQLite PC to Vercel/Neon | Offline encrypted transfer into a dedicated empty PostgreSQL database; web on Vercel, worker on PC |
| Vercel/PC worker to VPS using the same database | Stop the old worker, wait for leases, switch runtime configuration; no copy of data |
| Existing PostgreSQL to another PostgreSQL database | Offline backup and empty-target restore with matching release and keys |

Do not use another application's database. Verify provider pricing/account selection before provisioning. The connected Neon organization's paid plan was observed during development; a Lead Desk database has not been provisioned or connected.

## SQLite to PostgreSQL

Use the same release on both sides. Prepare private `source.env` and `target.env` files; both are ignored by Git. Each must explicitly contain `DATABASE_URL`, `SESSION_SECRET`, `BACKUP_KEY` and the original `ENCRYPTION_KEY` if credentials exist. Keep the encryption keys identical across profiles. The target must set `LIVE_SEND_ENABLED=false`; hosted profiles also need secure cookies and exact allowed hosts. Keep provider TLS parameters on the PostgreSQL URL and use the `postgresql+psycopg://` driver.

Enable source maintenance using its `.env`, stop every source web/worker process and wait for lease expiry as described in [operations](operations.md). Confirm that the target is empty and stopped. Then:

```sh
uv run python scripts/migrate_sqlite_to_postgres.py \
  --source-env source.env \
  --target-env target.env \
  --backup-file backups/cutover-001.wld \
  --offline-confirmed
```

PowerShell users can put the arguments on one line. The helper checks target emptiness before migration, writes a new encrypted source backup, migrates the target, restores it, and compares every row. Both databases remain in maintenance. It never overwrites the source, enables delivery or deletes existing target data.

Configure Vercel and the PC worker against the new PostgreSQL database, with the same credential key and appropriate mode-specific settings. The worker uses `APP_MODE=PC`; it must not use `VERCEL`. Follow [Vercel setup](deploy-to-vercel.md). Start web-only to inspect the restored workspace; then explicitly resume the chosen database. Keep live sending disabled until the [owner setup](owner-setup.md) checks are complete.

## Optional VPS processes

The repository includes service templates, not an installer:

- `scripts/systemd/webix-lead-desk-web.service`
- `scripts/systemd/webix-lead-desk-worker.service`
- `scripts/Caddyfile.example`

A server administrator must create a dedicated `lead-desk` OS user/group, install Python 3.12 and the pinned dependencies into `/opt/webix-lead-desk/.venv`, create a writable `/opt/webix-lead-desk/var`, and install the private `/etc/webix-lead-desk.env` with root-owned restrictive permissions. The unit's user needs access only to required application files; systemd reads the environment file. Set `APP_MODE=VPS`, a PostgreSQL URL, `COOKIE_SECURE=true`, exact `ALLOWED_HOSTS`, and the original keys. Keep `LIVE_SEND_ENABLED=false` during cutover.

Review paths, ownership and resource limits before copying the units. Run explicit migrations against the intended database before starting services. Caddy's example domain must be replaced with the actual approved hostname; configure DNS/TLS and expose only the reverse proxy. Uvicorn binds to loopback, accepts forwarded headers only from loopback and disables access logs. Do not add access logging that captures private concept bearer URLs. Set `PUBLIC_BASE_URL` to the actual HTTPS origin before publishing new concepts; keep old approved share URLs reachable if previously sent.

Stop the old worker before enabling the VPS worker, and wait for its lease to expire. Never run separate copies of a restored database as active senders. The database singleton lease prevents two workers on the same database; it cannot coordinate two disconnected databases.

## Acceptance and rollback

With live sending off, verify health, owner login/logout, counts, evidence, exact approval digests, sender locks, suppression and uncertain-email holds. Resume maintenance on the selected target, enqueue one health check and confirm it completes. Rehearsal tests exercise SQLite-to-PostgreSQL transfer in the isolated CI service; a real production transfer remains an explicit operating step.

Before any target writes or sends, rollback can retain the paused target and explicitly resume the original source after checking its state. Once the target accepts writes, it is the source of truth: pause it, preserve a fresh backup and reconcile all changes before rollback. Resuming an old source after new sends can cause duplicates. Never solve rollback by replaying uncertain delivery or deleting send history.
