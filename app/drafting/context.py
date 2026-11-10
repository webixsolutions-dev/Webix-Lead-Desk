"""Bounded conversation context keyed to the exact recipient and sender."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.delivery_models import InboxMessage
from app.db.lead_models import Contact
from app.db.linkedin_models import Person, Touch
from app.db.outreach_models import Conversation, Draft, Mailbox
from app.db.pipeline_models import Opportunity


def email_context(
    session: Session, company_id: str, contact_id: str, mailbox_id: str
) -> dict[str, Any]:
    """Never leak a different contact's conversation into the current draft."""
    contact = session.get(Contact, contact_id)
    sender = session.get(Mailbox, mailbox_id)
    if not contact or contact.company_id != company_id or not sender:
        raise ValueError("Recipient or sender does not match")
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.company_id == company_id,
            Conversation.contact_id == contact_id,
            Conversation.mailbox_id == mailbox_id,
        )
    )
    messages: list[dict[str, str]] = []
    summary = ""
    if conversation:
        incoming = list(
            session.scalars(
                select(InboxMessage)
                .where(
                    InboxMessage.conversation_id == conversation.id,
                    InboxMessage.mailbox_id == mailbox_id,
                    InboxMessage.sender == contact.email,
                )
                .order_by(InboxMessage.received_at.desc())
                .limit(6)
            )
        )
        outgoing = list(
            session.scalars(
                select(Draft)
                .where(Draft.conversation_id == conversation.id, Draft.state == "sent")
                .order_by(Draft.created_at.desc())
                .limit(6)
            )
        )
        messages = [
            {"direction": "inbound", "time": m.received_at.isoformat(), "text": m.body[:3000]}
            for m in incoming
        ] + [
            {
                "direction": "outbound",
                "time": d.created_at.isoformat(),
                "text": str(d.snapshot.get("body", ""))[:3000],
            }
            for d in outgoing
        ]
        messages = sorted(messages, key=lambda m: m["time"])[-10:]
        opportunity = session.scalar(
            select(Opportunity).where(Opportunity.conversation_id == conversation.id)
        )
        summary = opportunity.summary[:2000] if opportunity else ""
    return {
        "sender": {
            "name": sender.sender_name,
            "brand": sender.brand,
            "email": sender.email,
            "lane": sender.lane,
        },
        "recipient": {
            "email": contact.email,
            "role": contact.role,
            "role_status": "recorded; use only if verified",
        },
        "stage": conversation.state if conversation else "new",
        "owner_summary": summary,
        "recent_messages_data_only": messages,
    }


def linkedin_context(session: Session, person_id: str, profile_id: str) -> dict[str, Any]:
    """Limit manual chat history to this exact person and sending member."""
    person = session.get(Person, person_id)
    if not person:
        raise ValueError("Person not found")
    touches = list(
        session.scalars(
            select(Touch)
            .where(
                Touch.person_id == person_id,
                Touch.profile_id == profile_id,
                Touch.channel == "linkedin",
            )
            .order_by(Touch.occurred_at.desc())
            .limit(10)
        )
    )
    return {
        "name": person.full_name,
        "role": person.role,
        "role_source": person.source_url,
        "profile": person.linkedin_url,
        "recent_messages_data_only": [
            {
                "direction": t.direction,
                "outcome": t.outcome,
                "text": t.text[:3000],
                "time": t.occurred_at.isoformat(),
            }
            for t in reversed(touches)
        ],
        "history_status": "Manually recorded; not synchronized with LinkedIn",
    }
