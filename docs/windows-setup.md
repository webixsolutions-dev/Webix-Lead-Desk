# Install the Windows worker

Download `Webix-Lead-Desk-Setup-0.22.0.exe` from this repository's v0.22.0 release.

Updating an existing installation: press **Stop**, wait for the worker to finish, close the
companion, then run the new installer under the same Windows account. Saved connection details
and backups stay in place. Start the worker again. v0.22 can execute individually approved
test emails with saved HTML signatures from Settings; normal outreach remains off.
The release includes SHA256SUMS.txt. The installer bundles Python and all runtime
packages: no Python, uv, Git, PowerShell commands or PyPI download is needed on the
owner's PC. Internet is still needed for the hosted database and research jobs.
Windows x64 is tested. No paid software or signing certificate is required; this
build is unsigned, so Windows may show an unknown-publisher warning. Check the
release source/checksum before deciding whether to run it. Do not disable antivirus.

1. Run Setup as your normal Windows user; it does not require administrator access.
2. Open **Webix Lead Desk** from the desktop or Start menu.
3. Paste the same `DATABASE_URL` and `ENCRYPTION_KEY` configured on Vercel into the
   masked fields. Never send them in chat. Use the Session pooler URL with an encoded
   password, `postgresql+psycopg://` and `sslmode=require`.
4. Keep the actual hosted dashboard URL and choose a private backup folder.
5. Save settings, then use **Backup recovery key** to copy that key privately into
   your password manager. Losing it prevents restoring encrypted backups.
6. Click **Test connection**, then **Start Worker**. Open Dashboard and enqueue a
   health job. Verify completion in the hosted dashboard.
7. Optionally select **Start worker when I sign in to Windows** and save. This is
   sign-in startup, not boot-before-sign-in. Keep Windows signed in and awake.

Settings are stored in `%LOCALAPPDATA%\WebixLeadDesk\settings.dat`, protected with
Windows DPAPI for the current user. No plaintext `.env` is generated or imported.
Updates preserve settings and encryption/backup keys. Uninstall removes the app,
shortcuts and sign-in startup entry, but deliberately preserves private settings
and backups. Do not delete that folder if you need the keys.

**Stop** finishes the current operation and releases the worker lease before exit.
Closing the window asks to stop safely. Minimize it to keep working. Stop an old
PowerShell worker or Scheduled Task before starting this app; the shared database
lease also rejects concurrent workers. A shutdown/power loss can still interrupt
work; the existing uncertain-send and expired-lease safeguards remain in effect.

The desktop app keeps outreach, OpenAI and LinkedIn publishing off,
including when environment variables try to enable them. It does not create owner
accounts or automatically migrate production schemas. Those are administrator steps.
The existing PC/VPS CLI remains available; Vercel continues to serve only the web UI.
If a connection fails, recheck settings and database availability. Error messages
never show database URLs/passwords or raw provider exceptions.
