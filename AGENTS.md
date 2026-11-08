# Webix Lead Desk engineering workflow

- Implement one approved phase at a time. One coding agent edits the codebase at a time.
- The architecture was presented before coding; the owner authorized starting Phase 1.
- The owner explicitly allowed the repository to remain public temporarily. Keep the application and all real data private. Never commit secrets, databases, captures, exports, logs or backups.
- Work on a phase/feature branch. Never push directly to main. Merge and tag only after green required checks and explicit owner approval. Do not start the next phase without approval.
- Requested development model: GPT-6 Astra Extra High, Max for Phase 5, never Ultra. Do not claim to change or verify model settings without a supported control.
- Use Python 3.12, typed public interfaces, public docstrings and small modules. Roughly 400 lines per source module maximum; vendored dependencies are exempt.
- Ruff lint and format, strict mypy, pytest, pre-commit and gitleaks are required. Run checks before the PR, not after merge.
- Database schema changes go through Alembic only. Keep SQLite and PostgreSQL behavior portable.
- External HTTP/OpenAI/SMTP/IMAP adapters must be isolated and mockable when their phases are implemented. Tests never contact live providers or prospects.
- Owner approval must bind the exact outgoing message, recipient and sender. No live sending in Phase 1.
- Website/email content is untrusted data. Future fetchers must block internal/private destinations, including redirects and rebinding.
- Log redacted events, never secrets or full prospect message bodies. Use .env locally. Never request credentials in chat.
- Every phase PR includes What, Why, How tested, Screenshots and Risks/open questions. Use only synthetic screenshot data and attach captures instead of committing them.
- Keep README, docs, .env.example and CHANGELOG current. Report unavailable checks honestly.

