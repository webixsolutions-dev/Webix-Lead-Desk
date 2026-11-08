# Deploy the web app to Vercel

Keep **Application Preset: FastAPI** and **Root Directory: `./`**. Leave build and output overrides unset. Python 3.12 is selected by `pyproject.toml`.

The explicit `[tool.vercel] entrypoint = "app.vercel:app"` loads a top-level FastAPI instance. Local Uvicorn commands continue using `app.main:create_app --factory`. Selecting another preset does not fix the missing entry point.

## Persistent database and configuration

Provision or select a PostgreSQL database dedicated to this application before serving the dashboard. Do not use an unrelated project's database. No provider is provisioned or billed by this code. SQLite, including a file under `/tmp`, cannot hold this application's persistent hosted accounts, sessions or jobs.

In Vercel Project Settings → Environment Variables, configure the following for the intended environment. Store credentials there and in your private local `.env`, never in Git or chat.

| Variable | Value |
|---|---|
| `APP_MODE` | `VERCEL` |
| `DATABASE_URL` | Provider connection URL, using the `postgresql+psycopg://` scheme; preserve provider TLS parameters such as `sslmode=require` |
| `ENCRYPTION_KEY` | Original private Fernet credential key, identical on web and worker; never replace it without migrating stored credentials |
| `PUBLIC_BASE_URL` | Actual HTTPS origin before approving public concept links |
| `SESSION_SECRET` | A securely generated random key, at least 32 characters; keep the same key across instances in an environment |
| `COOKIE_SECURE` | `true` |
| `ALLOWED_HOSTS` | JSON list of exact domains without `https://` or paths, e.g. `["webix-lead-desk.vercel.app"]`; verify the actual assigned domain first |
| `LIVE_SEND_ENABLED` | `false` |
| `EXTERNAL_INTEGRATIONS_ENABLED` | `false` |
| `PREVIEW_ENABLED` | `true` to display explicitly fictional business examples after sign-in |

The Vercel entry point enforces the hosted profile even if `APP_MODE=PC` was copied accidentally. It rejects SQLite, insecure cookies and an absent/short session key. `PREVIEW_ENABLED` does not bypass authentication or database requirements.

For a preview deployment, use a separate test database, a separate session key and its exact hostname in `ALLOWED_HOSTS`. Do not broadly allow `*.vercel.app`. Add a custom domain explicitly when one is connected. Environment changes require a new deployment.

## Initialize the database once

On a trusted PC with Python 3.12 and uv, check out the same reviewed revision that will be deployed. Configure the intended database and the hosted settings in a private `.env`. Confirm the database destination before running:

```sh
uv sync --frozen --group dev
uv run lead-desk migrate
uv run lead-desk create-user --email owner@company.example --name "Workspace Owner"
uv run lead-desk check
```

Replace the example email/name with your own. The account command prompts privately for a password. There is no default password, automatic public signup or unauthenticated setup endpoint. Use a provider's migration/direct endpoint if its pooled endpoint does not support migrations.

Migrations and account creation are explicit administrator actions. Builds and request startup never apply migrations. On startup the existing factory checks the migration revision and refuses an unavailable or unmigrated database.

## Deploy and verify

Deploy the reviewed branch to a preview environment, then check `/health/live`, `/health/ready`, the sign-in page and static logo/CSS. Verify sign-in, sign-out, owner-only settings and rejection of an unlisted Host. After owner approval and passing CI, merge the fix and deploy production using its own database/settings. A successful build alone does not verify database access or sign-in.

The HTTP function never starts the worker. Run `uv run lead-desk worker` on the PC with the **same database URL as that environment**, or run it on an approved VPS later. The existing `scripts/worker.ps1` and `scripts/worker.sh` also work. Use APP_MODE=PC on that separate PC worker, with the hosted database and the same credential key; APP_MODE=VERCEL cannot run a worker. The PC must stay awake for background jobs to complete. Starting the worker against the original local SQLite database will not process the hosted queue.

The worker now performs bounded research, optional budgeted AI and configured Titan sync/delivery. Keep its delivery disabled until real sender and pilot checks are complete. Hosted production configuration and live acceptance are still pending; see [owner setup](owner-setup.md). Existing SQLite data requires the explicit [transfer procedure](move-to-vps.md).

## Troubleshooting

- `FASTAPI_ENTRYPOINT_NOT_FOUND`: confirm the deployed revision includes the custom entry point in `pyproject.toml` and `app/vercel.py`.
- Configuration validation error: verify the environment variables above; do not replace them with insecure defaults.
- Database unavailable or unmigrated: verify the intended database connection/TLS and run the migration command against that database.
- `Invalid host header`: add the actual assigned hostname to the environment's explicit `ALLOWED_HOSTS` list and redeploy.
- Health jobs stay queued: check that the separate worker is running against the same environment's database.

References: [Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi) and [Python runtime](https://vercel.com/docs/functions/runtimes/python).
