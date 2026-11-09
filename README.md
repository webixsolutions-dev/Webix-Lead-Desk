# Webix Lead Desk

A workspace for researching businesses, reviewing personalized outreach and following up on real conversations. Python 3.12, FastAPI, Jinja/HTMX, SQLAlchemy and Alembic; Webix red branding and the supplied logo.

**Phases 1–10 are implemented. Hosted account setup and live Titan/Hostinger/Gmail acceptance remain pending.** Neon is supported but not connected. No prospect has been contacted by the development workflow. Start locally without buying API access, or configure Vercel with a dedicated PostgreSQL database and a separate PC worker.

The repository is public temporarily at the owner's direction. The application requires private accounts. Real records, credentials, databases, screenshots, exports and backups must stay outside Git.

## What works

| Area | Behavior |
|---|---|
| Leads and campaigns | Editable targeting; bounded CSV/Open Calgary imports; deduplication, provenance, website/contact review and global suppression |
| Research | Bounded public website fetching; evidence, assessments, manual visual/social/ads observations and optional budgeted AI |
| Drafts and Playbook | Manual writing or copyable ChatGPT brief; optional API drafting; versioned guidance, editable offers/proofs and exact owner approval |
| Sending and inbox | Titan, Hostinger and Gmail SMTP/IMAP adapters, sender/thread locks, fresh-inbox checks, ramp/window limits, reply cancellation and uncertain-send holds |
| Pilot | Reviewed ten-business cohort, five-first-touch cap and explicit live readiness/acceptance gates |
| Pipeline and concepts | Assigned call cards, meetings, outcomes, approved concept links and separately approved same-thread messages |
| Partners and results | Separate sender lane, five-new-business daily cap, country review, manually submitted form records and actual activity reports |
| Learning | Suggestions from internal edits/rejections/outcomes; owner-approved guidance versions and restoration |
| Operations | Runtime/hold status, maintenance, encrypted backup/restore and SQLite-to-PostgreSQL transfer |

Examples are clearly fictional and separate from actual records. Automatic Google Maps collection, Firecrawl/Places adapters, social/ads browsing and automatic external form submission are **not implemented**. Contact forms use approved copy and manual outcome recording. The built-in website audit does not infer revenue, traffic, missed calls or ad spend. See [owner setup and integration limits](docs/owner-setup.md).

## Windows setup

Install Python 3.12 and Git. In PowerShell, from the project directory:

```powershell
py -3.12 -m pip install uv==0.13.5
uv sync --frozen --group dev
uv run python scripts/setup_local.py
uv run lead-desk migrate
uv run lead-desk create-user --email owner@company.example --name "Workspace Owner"
```

Replace the example identity with your own. The account command prompts privately for a password. There is no default password or public signup. Never paste credentials into chat or command arguments.

Start the dashboard and worker in separate terminals:

```powershell
.\scripts\start.ps1
```

```powershell
.\scripts\worker.ps1
```

Open **http://127.0.0.1:8000**. The dashboard binds to your PC only. A responsive phone layout does not create remote access. Keep the PC awake; sleeping or shutdown pauses work. Closing the browser does not stop an independently running worker.

If the PowerShell policy does not allow these scripts, use the equivalent commands in separate terminals without changing the policy:

```powershell
uv run python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
uv run lead-desk worker
```

Stop each with Ctrl+C. On macOS/Linux, use the same `uv` setup commands and `sh scripts/start.sh` / `sh scripts/worker.sh`.

## First use

Sign in, turn off fictional previews when ready, create a campaign and import a small reviewed list. Open Calgary imports require the running worker but no provider key. Confirm the business website, record contact publication/relevance evidence and audit the site. Write Playbook guidance and factual offers/proofs before drafting.

Every outgoing revision requires its own owner approval. Delivery also requires actual sender/provider setup, a fresh inbox, the pilot gate, suppression clearance and current contact evidence. Approval alone never overrides those conditions. Live delivery defaults off. The first pilot is smaller than the eventual 50-first-touch target; no sales or inbox-placement result is guaranteed.

To verify the worker without external calls, select **Run health check** in Outbox. Repeated clicks within a minute reuse one job. Real activity reports do not count synthetic preview examples.

Add a reviewer or recover owner access locally:

```sh
uv run lead-desk create-user --email reviewer@company.example --name "Team Reviewer" --role reviewer
uv run lead-desk reset-password --email owner@company.example
```

Password reset revokes that account's sessions. Local administrative commands assume access to the operating-system account that owns the installation.

## Configuration and runtime

`scripts/setup_local.py` creates `.env` once with separate random session, credential and backup keys; it preserves an existing file. `.env.example` documents settings. SQLite files live in ignored `var/` by default.

| Mode | Web | Database | Worker |
|---|---|---|---|
| PC only | Local loopback | SQLite | Separate PC process |
| Vercel + PC | Vercel FastAPI | Dedicated PostgreSQL, including Neon | PC using the same database, with APP_MODE=PC |
| VPS | HTTPS reverse proxy + FastAPI | PostgreSQL | Separate supervised service |

`APP_MODE` changes validation/runtime behavior; it does not transfer data or provision infrastructure. Vercel uses `app.vercel:app`, requires PostgreSQL/secure cookies/exact allowed hosts and never runs a worker. Keep `LIVE_SEND_ENABLED=false` there. The separate PC/VPS worker can enable delivery only after sender setup and actual approved test acceptance. The legacy `EXTERNAL_INTEGRATIONS_ENABLED` switch remains unsupported and false.

Optional OpenAI access needs separately verified API funding/model rates and a budget. Eligible ChatGPT Pro/Business subscriptions can claim API credits in a linked API organization. Record the actual available allowance and expiry; this app cannot infer a billing balance from a ChatGPT login. Set the linked organization/project spend controls and verify which are hard caps versus alert budgets; disable automatic recharge. The app allowance is a second, enforced reservation limit. The manual brief remains a fallback. No automatic provider purchases occur. Stored integration credentials are encrypted using the environment-held key; keep its recovery copy privately.

Use [Vercel setup](docs/deploy-to-vercel.md) for hosted configuration. A green build alone does not prove the database, login or worker works. Use [operations](docs/operations.md) for encrypted backup/restore and [cutover](docs/move-to-vps.md) to move data. Do not copy a live SQLite file or resume both sides of a database transfer.

## Development checks

Dependencies are pinned in `uv.lock`. Install [Gitleaks 8.24.3](https://github.com/gitleaks/gitleaks/releases/tag/v8.24.3) on PATH, then:

```sh
uv run pre-commit install
uv run ruff check app tests scripts
uv run ruff format --check app tests scripts
uv run mypy app tests scripts
uv run pytest -m "not postgres" --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=80
uv run python scripts/check_coverage.py
uv run pre-commit run --all-files
gitleaks git --redact --log-opts="--all"
```

CI checks lint/types, offline tests/coverage, PostgreSQL migrations and data transfer, Windows setup and secrets. Jobs, sending and inbox each require at least 80% coverage. Tests use synthetic records and fake providers; external socket calls are blocked. PostgreSQL tests accept only a disposable local database named `lead_desk_test` and recreate its schema. See [testing](docs/testing.md).

One coding agent, one phase branch and one reviewed PR at a time. The owner authorized sequential checked merges; specific outreach still requires in-app approval. The main-branch tag job creates the version from `pyproject.toml` only after required checks pass, and never moves a tag. See [engineering rules](AGENTS.md), [architecture](docs/architecture.md), [delivery plan](docs/delivery-plan.md) and [changelog](CHANGELOG.md).

## Module guides

- [Research](docs/research.md), [runtime architecture](docs/architecture.md)
- [Sender and pilot setup](docs/owner-setup.md), [pipeline/concepts](docs/pipeline.md)
- [Partners and forms](docs/partners.md), [reviewed memory](docs/learning.md)
- [Operations](docs/operations.md), [owner setup](docs/owner-setup.md)

HTMX is vendored with its license; assets are served locally. No analytics or tracking scripts are included.

## v0.11 review update

Per-mailbox provider presets, STARTTLS/SSL and IMAP SPECIAL-USE Sent discovery preserve
sender/thread locks. Client follow-ups use days 3/7/12/20 from accepted first contact; remove
20 for a three-follow-up sequence. Partner follow-ups use days 4/10. Edit offsets per campaign,
with at least two days after actual prior acceptance. Every step has its own exact approval;
concept and reply messages are separate kinds, not follow-up positions.

Automatic address syntax, role and MX/A-fallback checks run before approval and expire after
30 days. They establish domain routing, not inbox existence or contact permission. Negative
replies stop outreach; delayed delivery pauses that lead for owner review without a bounce.
Prices/guarantees are blocked across all steps; first contact has no URLs; concepts allow only
the approved concept link.

Settings includes versioned partner guidance, owner-recorded local team members, Learning
and owner-only Operations. Leads holds client/partner filters and Results. Six main tabs
remain. Defaults are `OPENAI_AUDIT_MODEL=gpt-6-luna` and
`OPENAI_DRAFT_MODEL=gpt-6.1-sol`; enter each model's current rates. Batch is optional for
audits only; drafts remain synchronous and unapproved. `OPENAI_ENABLED=false` stays default.

Stop the old worker, back up the database, deploy this version, run `uv run lead-desk migrate`
through `0012_outreach_spec`, and start one updated worker. Review per-mailbox limits and
readiness before enabling it. Hosting remains Vercel plus a separate PC/VPS worker; this
release does not provision Neon or turn the application into a Vercel-only background worker.


## v0.12 acquisition workflow

Choose a campaign industry and city, configure verified API funding/rates, and use
**Leads → Find businesses**. OpenAI Responses web search is the selected provider;
Google Places remains disabled until the owner explicitly enables its encrypted
connection and separate caps. No provider calls occur with the global AI switch off
or missing funding/prices. Start with one small batch, inspect the results, then
choose up to ten batches. Discovery stops on empty/duplicate-only results or a cap.

Official-site matching requires visible business identity and location. Ambiguous
results require review. Confirmed sites continue through website audit, contact
extraction, rule score and optional AI assessment/draft. An eligible sender must be
configured; every message still needs exact owner approval.

Rule scores work offline, show their sources and retain versioned history. Confirm
no-website status and listing observations on the lead page. Select the website +
Google Business Profile package from your Offer Library. **Today → Calls** provides
a printable/mobile list, human outcomes and consent capture; there is no automated
dialer. Reviewers can use Calls but cannot open Settings. AI note cleanup uses the
draft model and produces suggestions or an unapproved email draft.

Every lead has an event-derived stage bar, next action, score history and a linked
timeline in Calgary/Pakistan time. Migrations 0013/0014 backfill existing records and
enforce append-only event storage. Read [the acquisition guide](docs/acquisition.md)
for setup, provider retention rules, limitations and acceptance checks.


### LinkedIn L1

People & LinkedIn on each business opens proposed and confirmed people. Register real
staff profiles in Playbook & Settings → LinkedIn team profiles. No password, cookie,
OAuth or LinkedIn developer credential is needed for L1. Owner search uses the existing
funded web-search quotas and leaves every result unconfirmed. See [LinkedIn setup](docs/linkedin.md).

LinkedIn L2 adds a persistent weekday planner under Today → LinkedIn, owner-lowered
profile quotas and a shared named-person/day email fence. Human action execution remains
pending L3. Windows startup installation and recovery details are in [docs/linkedin.md](docs/linkedin.md).


### LinkedIn L3 — review, then a human action

Open **Outbox → LinkedIn approvals & tasks**. Review a planned task, save its exact
copy and approve that revision. Direct messages require reviewed evidence, a complete
business identity and a contact basis specifically applicable to that LinkedIn person.
Email publication alone is not a LinkedIn permission record. First-message bodies are
150–200 characters; the mandatory identity and reply-to-stop footer remains separate.

Members see only their approved queue and assigned calls. **Start my action** reserves
the person/day and profile quota; the member opens LinkedIn, copies the approved text,
and confirms the real action. No automated invitation, DM, comment or profile visit is
performed. An expired action is held for owner checking, never blindly repeated. The
application fences task starts; it cannot control actions performed outside its UI.

Any recorded reply stops pending company outreach. Review LinkedIn replies in
**Replies & Pipeline** and prepare a separately approved response when appropriate.
Member voice cards live in Settings. Optional AI improves an unapproved brief with the
configured draft model; the copyable concept/walkthrough prompt uses no provider API.
The monthly AI ceiling is $170 with separate category ceilings, Calgary month boundaries,
no carryover, and full reservation of unknown usage. Verified funding and configured
rates remain required; a ChatGPT subscription or promotional balance is not inferred.

Run `lead-desk migrate` before restarting the dashboard/worker after this release.
L4 official posting/OAuth and L5 channel reporting are subsequent phases.
