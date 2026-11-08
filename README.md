# Webix Lead Desk

A focused workspace for understanding businesses, reviewing outreach and keeping the next conversation moving.

**Phase 2 adds real discovery to the foundation.** It includes real sign-in, owner/reviewer roles, saved preferences, a responsive six-tab interface, database migrations and a durable health-job worker. Business cards and outreach shown in preview mode are fictional. Campaigns, CSV import, Open Calgary discovery, website/contact review and global suppression are now available. AI, mailbox connections and sending arrive in subsequent phases.

The repository is public temporarily at the owner's direction. The running application is private, and real data, credentials, screenshots and databases stay outside Git.

## Windows setup

Install Python 3.12 and Git. In PowerShell, from the project directory:

```powershell
py -3.12 -m pip install uv==0.13.5
uv sync --frozen --group dev
uv run python scripts/setup_local.py
uv run lead-desk migrate
uv run lead-desk create-user --email owner@company.example --name "Workspace Owner"
```

Replace the example email/name with your own. The command prompts for a password locally without showing it. Use 12–256 characters. There is no default password or public sign-up. Do not paste credentials into chat or command arguments.

Start the web app in one terminal:

```powershell
.\scripts\start.ps1
```

Start the worker in a second terminal:

```powershell
.\scripts\worker.ps1
```

Open **http://127.0.0.1:8000** and sign in. The server binds to your PC only. A responsive phone layout does not create remote access. Keep both processes running while using the workspace. Closing the browser is fine; sleeping or shutting down the PC pauses work.

If your existing PowerShell policy does not permit scripts, use the equivalent Python commands without changing that policy:

```powershell
uv run python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
uv run lead-desk worker
```

Run the two commands in separate terminals. Stop each with Ctrl+C.

## macOS / Linux setup

With Python 3.12 and uv 0.13.5 installed:

```sh
uv sync --frozen --group dev
uv run python scripts/setup_local.py
uv run lead-desk migrate
uv run lead-desk create-user --email owner@company.example --name "Workspace Owner"
sh scripts/start.sh
# In another terminal:
sh scripts/worker.sh
```

## What works now

| Tab | Current behavior |
|---|---|
| Today | Fictional review queue, clearly labeled preview metrics, real database/worker status |
| Leads | Campaigns, CSV/city imports, website and contact review, suppression; separate fictional examples |
| Outbox | Non-sendable example steps and real health checks with automatic status refresh |
| Replies & Pipeline | Labeled preview of the future reply and call handoff |
| Demos | Future concept types and an honest empty state |
| Playbook & Settings | Real persisted preferences, preview toggle and disabled integration placeholders |

To verify the worker, open Outbox and select **Run health check**. A queued item will complete when the worker is running. It makes no external requests. Duplicate clicks within a minute create one job. Only owners can edit settings or enqueue checks.

Add a reviewer locally:

```sh
uv run lead-desk create-user --email reviewer@company.example --name "Team Reviewer" --role reviewer
```

Recover access locally, revoking all existing sessions for that account:

```sh
uv run lead-desk reset-password --email owner@company.example
```

Local administrator commands assume access to the operating-system account that owns the application. There is no remote password-reset endpoint.

## Configuration and data

`scripts/setup_local.py` creates `.env` once with a random signing key. It preserves an existing file. `.env.example` documents the environment variables. Database files live in `var/` by default and are ignored by Git.

- Preferences in the dashboard override environment defaults for workspace name, timezones and preview visibility.
- `APP_MODE=PC` is the pilot default. VPS and Vercel profiles require PostgreSQL and secure cookies, but do not deploy or migrate anything automatically.
- `LIVE_SEND_ENABLED` and `EXTERNAL_INTEGRATIONS_ENABLED` must remain false. This release refuses to start otherwise.
- `SESSION_SECRET` is required. The database stores password hashes and hashed bearer tokens, never plaintext passwords or bearer tokens.
- All timestamps are stored in UTC. IANA timezones handle Calgary daylight saving and local display time.
- No automatic provider payments, account creation or purchases occur.

Take a backup only with both processes stopped. Copy `var/` and `.env` to private storage outside the repository and protect it with your operating system's access controls/encryption. SQLite WAL/journal files must remain with the database if present. Backups contain sensitive data and are never Git commits. Restore is a supervised local operation; VPS transfer is a later approved phase.

## Vercel web hosting

Select the **FastAPI** preset with root directory `./`. The explicit `app.vercel:app` entry point supplies the top-level instance that Vercel needs. Configure a dedicated PostgreSQL database, a session key, secure cookies and exact allowed domains before deployment; see [Vercel setup](docs/deploy-to-vercel.md). The web function does not start the worker or migrate a database. A PC worker can process hosted health jobs only when configured against that same PostgreSQL database.

## Development checks

The Python dependencies are locked in `uv.lock`. Install [Gitleaks 8.24.3](https://github.com/gitleaks/gitleaks/releases/tag/v8.24.3) from the official release and put its executable on PATH, then:

```sh
uv run pre-commit install
uv run ruff check app tests scripts
uv run ruff format --check app tests scripts
uv run mypy app tests
uv run pytest -m "not postgres" --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=80
uv run python scripts/check_coverage.py
uv run pre-commit run --all-files
gitleaks git --redact --log-opts="--all"
```

Hooks run Ruff, Ruff formatting, mypy and gitleaks. The secret hook inspects staged changes. The history scan inspects commits. Never bypass a failed secret scan. CI runs these checks plus an isolated PostgreSQL migration check and a Windows setup smoke test.

Automated tests use disposable databases and synthetic users. They make no live email or external API calls. The PostgreSQL test accepts only a local database named `lead_desk_test` and explicitly downgrades/recreates its schema. See [testing](docs/testing.md) before running it.

## Architecture and workflow

See [architecture](docs/architecture.md), [decisions](docs/decisions/0001-foundation.md), [VPS migration plan](docs/move-to-vps.md) and [engineering instructions](AGENTS.md).

One coding agent and one phase at a time. The owner authorized sequential implementation and merges on 8 November 2026. Changes belong on phase/feature branches. Every phase ends in a PR with a self-review. Passing required checks are necessary before merge and a version tag. Exact outgoing messages still need owner approval.

The owner approved the initial Phase 1 publication to main after the Webix logo/red branding update. The CI tag job creates the version in `pyproject.toml` once, only after every quality job passes on main. Only that job has repository write permission; it never moves an existing tag or approves later phases.

Vendored HTMX 2.0.8 is included with its license and is used only for authenticated worker-status polling. Assets are served locally without a CDN. No analytics or tracking scripts are included.


## Campaigns and source imports

Open Leads, create a campaign, then import UTF-8 CSV (name, city, industry; optional website, email, phone, address, source_key, source_url). A file picker fills an editable CSV preview; without JavaScript paste the CSV. Imports are atomic, capped at 500 rows/1 MB and never authorize delivery. Reimports reuse source identities and campaign memberships. Shared domains stay separate and are flagged for review.

Open Calgary discovery queues a job for the PC worker. Use city licence categories such as CONTRACTOR or CLEANING; the dataset only covers Calgary and does not supply verified websites or contact permission. Inspect each business, confirm its website with evidence, and record role relevance and publication restrictions for contacts. Global suppression applies across all campaigns. Current results show at most 100 businesses; use campaign/name filters.

Phase 2 source acceptance imported ten public licensed-business candidates into a separate private local acceptance database, with no outreach. Full hosted testing awaits database and account configuration. See docs/delivery-plan.md for the remaining sequence and owner setup items.


### Phase 3: website research

Open an imported lead, confirm its business website, and choose **Research website**.
The separate worker reads robots.txt and at most three HTML pages. Redirects stay on
the reviewed origin. Fresh successful audits are reused for 30 days unless refreshed.
Page elements are observations, not claims of revenue, traffic, mobile quality or form delivery.
Add dated visual/social/ads observations manually with a source URL. Extracted email
addresses remain unverified and require contact-basis review before outreach.

AI classification is optional and disabled by default. Verify a dedicated API project's
available funds, expiration and current model rates; record the allowance in Settings;
then configure `OPENAI_*` variables from `.env.example`. Never paste keys into chat.
The research budget pauses at 90% of the lifetime allowance. Each call reserves an
overestimated maximum cost before network access. Unknown outcomes retain the reservation
and cannot be reissued with the same request key. No auto-recharge or model name is assumed.
See [research controls](docs/research.md) for limitations and acceptance checks.
