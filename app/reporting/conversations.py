"""One review card per confirmed recipient; cross-channel matching uses explicit contact links."""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.delivery_models import InboxMessage, SendIntent
from app.db.lead_models import Company, Contact
from app.db.linkedin_models import LinkedInTask, Person, Touch
from app.db.outreach_models import Conversation, Draft
from app.db.session import Database


def threads(db: Database, now: datetime) -> list[dict[str, Any]]:
    """Project email and LinkedIn without joining unrelated people at the same business."""
    with db.sessions() as session:
        people = {p.id: p for p in session.scalars(select(Person))}
        linked = {p.contact_id: p for p in people.values() if p.contact_id}
        companies = {c.id: c for c in session.scalars(select(Company))}
        contacts = {c.id: c for c in session.scalars(select(Contact))}
        conversations = {c.id: c for c in session.scalars(select(Conversation))}
        cards: dict[str, dict[str, Any]] = {}
        for conversation in conversations.values():
            contact = contacts[conversation.contact_id]
            person = linked.get(contact.id)
            key = "person:" + person.id if person else "contact:" + contact.id
            cards[key] = {
                "key": key,
                "name": person.full_name if person else contact.email,
                "company": companies[conversation.company_id].name,
                "url": "/conversations/contact/" + contact.id,
                "channels": {"Email"},
                "drafts": 0,
                "replies": 0,
                "scheduled": 0,
                "last_at": conversation.created_at,
            }
        for person in people.values():
            cards.setdefault(
                "person:" + person.id,
                {
                    "key": "person:" + person.id,
                    "name": person.full_name,
                    "company": companies[person.company_id].name,
                    "url": "/conversations/people/" + person.id,
                    "channels": set(),
                    "drafts": 0,
                    "replies": 0,
                    "scheduled": 0,
                    "last_at": person.verified_at,
                },
            )

        def email_card(conversation_id: str) -> dict[str, Any]:
            conversation = conversations[conversation_id]
            person = linked.get(conversation.contact_id)
            return cards["person:" + person.id if person else "contact:" + conversation.contact_id]

        for draft in session.execute(
            select(Draft.conversation_id, Draft.state, Draft.created_at).where(
                Draft.state != "superseded"
            )
        ):
            card = email_card(draft.conversation_id)
            card["drafts"] += draft.state == "draft"
            card["last_at"] = max(card["last_at"], draft.created_at)
        for message in session.execute(
            select(
                InboxMessage.id,
                InboxMessage.conversation_id,
                InboxMessage.sender,
                InboxMessage.mailbox_id,
                InboxMessage.classification,
                InboxMessage.received_at,
            )
        ):
            incoming_conversation = conversations.get(message.conversation_id or "")
            if (
                incoming_conversation
                and contacts[incoming_conversation.contact_id].email == message.sender
            ):
                card = email_card(incoming_conversation.id)
            else:
                key = "unmatched:" + message.mailbox_id + ":" + message.sender
                card = cards.setdefault(
                    key,
                    {
                        "key": key,
                        "name": message.sender,
                        "company": "Sender identity needs review",
                        "channels": {"Email"},
                        "drafts": 0,
                        "replies": 0,
                        "scheduled": 0,
                        "last_at": message.received_at,
                        "url": "/command/replies/email/" + message.id,
                    },
                )
                if message.received_at >= card["last_at"]:
                    card["url"] = "/command/replies/email/" + message.id
            card["replies"] += message.classification not in {"automatic", "bounce"}
            card["last_at"] = max(card["last_at"], message.received_at)
        for intent in session.execute(
            select(SendIntent.conversation_id, SendIntent.state, SendIntent.accepted_at)
        ):
            card = email_card(intent.conversation_id)
            card["scheduled"] += intent.state in {"scheduled", "transmitting"}
            if intent.accepted_at:
                card["last_at"] = max(card["last_at"], intent.accepted_at)
        for touch in session.scalars(
            select(Touch).where(Touch.person_id.is_not(None), Touch.channel == "linkedin")
        ):
            card = cards["person:" + str(touch.person_id)]
            card["channels"].add("LinkedIn")
            card["replies"] += touch.direction == "inbound"
            card["last_at"] = max(card["last_at"], touch.occurred_at)
        for task in session.scalars(
            select(LinkedInTask).where(LinkedInTask.state.in_(["pending", "ready", "in_progress"]))
        ):
            card = cards["person:" + task.person_id]
            card["channels"].add("LinkedIn")
            card["drafts"] += not bool(task.approval_digest)
            card["last_at"] = max(card["last_at"], task.created_at)
    return sorted(
        [row for row in cards.values() if row["channels"]],
        key=lambda row: row["last_at"],
        reverse=True,
    )


def thread(db: Database, *, contact_id: str = "", person_id: str = "") -> dict[str, Any]:
    """Load exact recipient history, retaining channel/member identity on each message."""
    with db.sessions() as session:
        person = (
            session.get(Person, person_id)
            if person_id
            else session.scalar(select(Person).where(Person.contact_id == contact_id))
        )
        linked_contact = contact_id or (person.contact_id if person else "")
        contact = session.get(Contact, linked_contact) if linked_contact else None
        if not person and not contact:
            raise ValueError("Recipient not found")
        company = session.get(Company, person.company_id if person else contact.company_id)  # type: ignore[union-attr]
        conversation = (
            session.scalar(select(Conversation).where(Conversation.contact_id == contact.id))
            if contact
            else None
        )
        messages: list[dict[str, Any]] = []
        drafts = []
        intents = []
        if conversation and contact:
            for message in session.scalars(
                select(InboxMessage).where(
                    InboxMessage.conversation_id == conversation.id,
                    InboxMessage.sender == contact.email,
                )
            ):
                messages.append(
                    {
                        "channel": "Email",
                        "direction": "inbound",
                        "text": message.body,
                        "at": message.received_at,
                        "sender": message.sender,
                    }
                )
            drafts = list(
                session.scalars(
                    select(Draft)
                    .where(Draft.conversation_id == conversation.id, Draft.state != "superseded")
                    .order_by(Draft.created_at.desc())
                )
            )
            intents = list(
                session.scalars(
                    select(SendIntent)
                    .where(SendIntent.conversation_id == conversation.id)
                    .order_by(SendIntent.due_at)
                )
            )
            for draft in drafts:
                if draft.state == "sent":
                    messages.append(
                        {
                            "channel": "Email",
                            "direction": "outbound",
                            "text": draft.snapshot.get("body", ""),
                            "at": draft.created_at,
                            "sender": draft.snapshot.get("from_email", ""),
                        }
                    )
        tasks = []
        if person:
            for touch in session.scalars(
                select(Touch).where(Touch.person_id == person.id, Touch.channel == "linkedin")
            ):
                messages.append(
                    {
                        "channel": "LinkedIn",
                        "direction": touch.direction,
                        "text": touch.text or touch.outcome.replace("_", " "),
                        "at": touch.occurred_at,
                        "sender": "Recorded LinkedIn action",
                    }
                )
            tasks = list(
                session.scalars(
                    select(LinkedInTask).where(
                        LinkedInTask.person_id == person.id,
                        LinkedInTask.state.in_(["pending", "ready", "in_progress"]),
                    )
                )
            )
    return {
        "person": person,
        "contact": contact,
        "company_record": company,
        "thread_messages": sorted(messages, key=lambda row: row["at"]),
        "thread_drafts": drafts,
        "thread_intents": intents,
        "thread_tasks": tasks,
    }
