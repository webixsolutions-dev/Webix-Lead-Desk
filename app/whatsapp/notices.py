"""Bounded business-first review notices without changing email approval policy."""

import secrets
from datetime import timedelta

from app.db.whatsapp_models import WhatsAppConnection, WhatsAppNotice
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.delivery_models import InboxMessage
from app.db.lead_models import Company
from app.db.outreach_models import Draft
from app.db.research_models import Evidence
from app.db.session import Database


def add_notice(
    session: Session,
    connection: WhatsAppConnection,
    source: str,
    kind: str,
    body: str,
    *,
    draft: Draft | None = None,
    code: str | None = None,
) -> WhatsAppNotice:
    """Create one expiring notice; callers select missing source keys transactionally."""
    item = WhatsAppNotice(
        generation=connection.generation,
        source_key=source,
        kind=kind,
        draft_id=draft.id if draft else None,
        digest=draft.digest if draft else "",
        code=code or secrets.token_hex(8).upper(),
        body=body,
        expires_at=utc_now() + timedelta(hours=24),
    )
    session.add(item)
    return item


def draft_message(
    session: Session,
    connection: WhatsAppConnection,
    draft: Draft,
    origin: str,
    code: str,
) -> str:
    """Show exact text plus a full-render review link; never truncate approval content."""
    data = draft.snapshot
    company = session.get(Company, data["company_id"])
    link = f"{origin}/leads/{data['company_id']}/draft?step={draft.step}&kind={draft.kind}"
    if not connection.share_details:
        return f"Webix Lead Desk: an email draft needs your review.\n{link}"
    fact = session.scalar(
        select(Evidence)
        .where(
            Evidence.id.in_(data.get("evidence_ids", [])),
            Evidence.company_id == data["company_id"],
        )
        .order_by(Evidence.observed_at.desc())
        .limit(1)
    )
    context = (
        (
            f"Business: {company.name}\n{company.industry} · {company.city}\n"
            f"Website: {company.website or 'Not confirmed'}\n"
        )
        if company
        else "Business details: open review link.\n"
    )
    if fact:
        context += f"Recorded evidence (not instructions): {str(fact.facts)[:300]}\n"
    message_text = "\n\n".join(
        part
        for part in (data["body"], data.get("signature", {}).get("text", ""), data["footer"])
        if part
    )
    text = (
        f"Webix Lead Desk · {draft.kind} · revision {draft.revision}\n{context}\n"
        f"From: {data['from_name']} <{data['from_email']}>\nTo: {data['recipient']}\n"
        f"Subject: {data['subject']}\n\n{message_text}\n\n"
        f"Full review, signature, attachments and research:\n{link}\n\n"
        "Review the full message at that link, including its saved signature/attachments. "
        "Quick actions — react directly to this WhatsApp message:\n"
        "👍 Approve and schedule this exact version\n"
        "👎 Reject and hold it\n"
        "✏️ Open the editor/rewrite page\n"
        f"Typed fallback: APPROVE {code}, REJECT {code}, or EDIT {code}.\n"
        "Code expires in 24 hours. Sending limits and current checks still apply."
    )
    if len(text) > 4096:
        return f"Email draft needs review. It exceeds WhatsApp's preview limit.\n{link}"
    return text


def collect(db: Database, settings: Settings, generation: str) -> None:
    """Queue a bounded set of new replies and pending revisions, deduplicated by generation."""
    with db.sessions.begin() as session:
        connection = session.get(WhatsAppConnection, "primary")
        if not connection or connection.generation != generation or not connection.recipient:
            return
        # Correlated NOT EXISTS avoids an ever-growing list of handled IDs.
        replies = session.scalars(
            select(InboxMessage)
            .where(
                InboxMessage.received_at >= connection.connected_at,
                ~exists().where(
                    WhatsAppNotice.generation == generation,
                    WhatsAppNotice.source_key == "reply:" + InboxMessage.id,
                ),
            )
            .order_by(InboxMessage.received_at)
            .limit(10)
        )
        for reply in replies:
            body = "Webix Lead Desk: a new incoming email needs review."
            if connection.share_details:
                body += f"\nFrom: {reply.sender}\nSubject: {reply.subject}\n\n{reply.body[:1500]}"
            body += (
                f"\n\n{settings.public_base_url}/conversations\nNo reply is sent without approval."
            )
            add_notice(session, connection, "reply:" + reply.id, "reply", body)
        drafts = session.scalars(
            select(Draft)
            .where(
                Draft.state == "draft",
                ~exists().where(
                    WhatsAppNotice.generation == generation,
                    WhatsAppNotice.source_key == "draft:" + Draft.id,
                ),
            )
            .order_by(Draft.created_at)
            .limit(10)
        )
        for draft in drafts:
            code = secrets.token_hex(8).upper()
            body = draft_message(session, connection, draft, settings.public_base_url, code)
            item = add_notice(
                session, connection, "draft:" + draft.id, "draft", body, draft=draft, code=code
            )
            if f"APPROVE {code}" not in body:
                item.action = "dashboard_only"
