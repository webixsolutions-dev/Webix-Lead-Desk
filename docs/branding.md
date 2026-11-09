# Webix Solutions branding

Use the owner-supplied Webix Solutions identity. The dashboard and sign-in screen use `app/ui/static/webix-logo.png`, a transparent cutout prepared from the supplied logo. Preserve its aspect ratio and transparent background. The compact SVG favicon follows the supplied angular emblem.

- Main brand red: `#ec1e28`, sampled from the supplied artwork.
- Primary button red: `#d71923`, for readable white text.
- Main text: `#222224`.
- Canvas: `#f7f7f8`; light sidebar and white cards keep the black wordmark legible.
- Use pale red surfaces for selected navigation, preview notices and supporting cards. Green remains available for successful system status.

The logo has no white bitmap background. Its light surrounding surface belongs to the interface and can change without leaving a white rectangle around the artwork.

## Dashboard layout (v0.20)

The Today page starts with one next action determined by real business/draft counts.
Four linked metrics follow, then the message review queue and compact Calls, LinkedIn
and Launch checklist links. The worker delivery label uses only a current worker
lease; an absent or expired report reads "Not reported", never "Off" or "On".

Leads keeps search and campaign filters in one form, with optional filters and an
expandable Add leads area. Business cards show their current stage first; the complete
stage checklist is available under View progress. No source or form action is removed.

Settings uses native details groups: Email setup; Writing & offers; Discovery & AI;
Learning & Memory; Workspace preferences; System & backups. Sender identities precede
mailbox connection steps. Hash links such as `/settings#mail-delivery` open parent
groups automatically. All sections remain operable without JavaScript.

Healthy infrastructure details live in System & backups, with a concise receipt on
Today. Outstanding hosted warnings stay visible on every owner page and automatically
expand the system group in Settings. Reviewer permissions are unchanged.

`workspace.css` supplies the shared layout after the existing component styles. Mobile
navigation has a close button, backdrop, Escape handling and a keyboard focus loop.
No new provider calls, dependencies, environment variables or migrations are required.
