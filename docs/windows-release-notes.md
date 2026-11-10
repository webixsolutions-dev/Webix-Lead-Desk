v0.23 adds reusable service packages, custom quotes, offer currencies and proof matched to
campaign services and industries. It retains v0.22 HTML mailbox signatures.

Stop the worker and install this version once. Saved settings remain. The new schema is
required; start the updated companion to run the versioned migration before using the dashboard.
An older companion cannot migrate the newer schema. Future edits to offers, proof and guidance
are saved in the shared database and do not require another installation. No outreach is enabled
or sent by this update. New country sending rules and currency conversion are not included.

Install the bundled Windows worker without Python, uv, Git or terminal setup.

- Red/white Webix companion with masked setup fields, Test connection, Start/Stop and Open Dashboard.
- Current-Windows-user encrypted settings and private backup recovery key access.
- Cooperative shutdown, existing database worker lock and optional sign-in startup.
- Weekly encrypted backups using the existing scheduler. Outreach and paid integrations remain off;
  only separately approved fixed-content diagnostic emails can send.
- Tested on Windows: bundled migrations, SQLite health job, psycopg driver, DPAPI, GUI launch, installer/upgrade/uninstall preservation.

Read docs/windows-setup.md first. This build is unsigned; verify its source and SHA256SUMS.txt. Private settings and backups are preserved on uninstall. Sign-in startup needs a signed-in Windows user. Real Supabase connection and an owner's Windows PC remain an owner acceptance step.
