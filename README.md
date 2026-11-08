# Webix Lead Desk

A focused workspace for understanding businesses, reviewing outreach and keeping the next conversation moving.

**Phase 6 adds a controlled pilot and truthful launch readiness.** The app includes sign-in,
owner/reviewer roles, campaigns, CSV/Open Calgary imports, website evidence, optional budgeted
AI, versioned Playbook guidance, editable offers/proofs and sender profiles. Draft manually,
use a copyable ChatGPT brief, or enable the API after verifying funding. Every revision needs
its own owner approval. Live SMTP delivery defaults off until sender setup and test acceptance are complete.
Preview examples are fictional and separate from actual imported records.

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


### Phase 4: drafting and owner review

1. Add a real Titan sender identity in Settings. Its mailing address and contact details form
   the reviewed footer. Saving a profile does not connect the mailbox.
2. Publish global or campaign-specific Playbook guidance. Keep genuine examples here; website
   content cannot update this guidance. Offers use your own CAD prices; first-touch copy omits them.
3. Open a researched lead and choose **Write a draft**. Cite stored evidence IDs. Paste your own
   copy, use the copyable ChatGPT brief, or choose optional configured AI generation.
4. Save and inspect the complete sender, recipient, subject, body and footer. Confirm contact
   verification and relevant permission evidence before approving. A reviewer can draft; only
   the owner can approve. Every edit supersedes the old revision and needs fresh approval.

Draft approvals are not blanket permission for a campaign or sequence. A reply, suppression,
changed sender profile, expired contact evidence or later revision can still block dispatch.


### Phase 5: delivery and inbox

The web process never sends mail. A single PC/VPS worker reads Titan IMAP before each
scheduled delivery and requires an owner-approved exact draft, verified contact basis,
ready sender, current inbox, Calgary weekday window and available capacity. Enable
`LIVE_SEND_ENABLED=true` only on that worker after completing the sender checklist in Settings.
Keep it false in Vercel. Create a dedicated Fernet `ENCRYPTION_KEY` in private configuration;
the web app and worker must share it. `scripts/setup_local.py` generates it for new installs.
Never replace an existing encryption key without decrypting/re-encrypting stored credentials.

Settings accepts write-only Titan app passwords and optional OpenAI/Firecrawl/Places keys.
Saved OpenAI credentials still require enabled model settings, current prices and verified funding.
Firecrawl and Places are saved only; their paid discovery adapters remain disabled.
Titan requires third-party access; use an app password if the account has 2FA. The supported
pilot uses smtp.titan.email:465 and imap.titan.email:993 with verified TLS. EU-region Titan
endpoints are not yet supported. No mailbox credentials are present in this repository.

Outbox → Schedule approved message queues one exact revision. Follow-ups require their own
approval, the original sender and subject, and at least three days after confirmed delivery.
The pilot permits only one follow-up. Daily ceilings ramp 10/20/35/50 across four weeks,
counting follow-ups and conservative attempt reservations; the owner can lower them.
Messages start Monday–Friday 08:30–11:30 America/Edmonton, 3–8 minutes apart. There is no
catch-up burst. Three distinct bounces pause the mailbox until the next Calgary day.

Every correlated reply stops pending outreach before classification. Unsubscribe and not-interested
responses also suppress the recipient globally. Replies render escaped plain text only.
An ambiguous SMTP outcome or expired worker during transmission is held without an automatic
retry. Check Sent evidence searches Titan's Sent folder for the exact stable Message-ID,
sender, recipient, subject and content. No match never means it is safe to resend.

Offline acceptance uses loopback SMTP/IMAP servers and synthetic inboxes. Real mailbox/DNS,
provider limits, deliverability and owner-approved recipient acceptance remain deployment steps.
See [delivery safety](docs/decisions/0005-delivery-safety.md).


### Phase 6: small pilot and readiness

Open **Today → Review launch readiness**. This screen separates database/encryption,
worker heartbeat/live switch, stored Titan credentials, owner-attested sender checks,
complete inbox sync and pilot review. It does not claim to verify billing or DNS automatically.

Review ten imported businesses with current evidence and a verified website match:
4 HVAC, 4 roofing and 2 cleaning. Activating freezes that cohort and permits at most five
first-contact businesses; every draft still needs exact owner approval and scheduling.
The pilot reservation is atomic, counts uncertain attempts conservatively, and survives retries.
Pausing blocks new transmissions and follow-ups. Completion requires at least one confirmed
accepted pilot first-touch and no uncertain or in-progress pilot transmission, plus explicit owner acceptance.
Completing a pilot does not approve later email content or change ramp/recipient checks.

Today counts actual imported companies, unapproved drafts, Calgary-day accepted sends and
recorded replies. Fictional samples are separate and never count toward results.
No live pilot has been launched by this repository release: credentials, actual cohort review,
recipient/message approvals and provider acceptance remain pending in the installed workspace.
