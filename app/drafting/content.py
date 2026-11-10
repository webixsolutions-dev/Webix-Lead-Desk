"""Plain-text content checks and canonical exact-message fingerprints."""

import hashlib
import json
import re
from typing import Any

from app.db.outreach_models import Mailbox


def footer(mailbox: Mailbox) -> str:
    """Produce the mandatory identity and reply-based unsubscribe footer."""
    return (
        f"{mailbox.sender_name} | {mailbox.brand}\n{mailbox.mailing_address}\n"
        f"{mailbox.contact_details}\nTo stop these emails, reply unsubscribe."
    )


def fingerprint(snapshot: dict[str, Any]) -> str:
    """Bind approval to canonical UTF-8 content and routing metadata."""
    raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def check_copy(
    subject: str, body: str, *, first_touch: bool = True, allow_price: bool = False
) -> None:
    """Reject header injection, empty or oversized copy and outreach price promises."""
    if not 3 <= len(subject.strip()) <= 150 or any(c in subject for c in "\r\n\x00"):
        raise ValueError("Use a single-line subject of 3–150 characters")
    if not 10 <= len(body.strip()) <= 5000 or "\x00" in body or len(body.split()) > 120:
        raise ValueError("Keep the message body between 10 characters and 120 words")
    if re.search(r"<\s*(html|img|script|iframe|a\b)", body, re.I):
        raise ValueError("Use plain text without HTML or images")
    if re.search(r"\bguarantee(?:d)?\b", subject + " " + body, re.I):
        raise ValueError("Do not promise guaranteed outcomes")
    if (first_touch or not allow_price) and re.search(
        r"[$€£]|\b(?:CAD|USD)\s*\d|\b\d[\d,.]*\s*(?:CAD|USD|dollars?)\b|\bguarantee(?:d)?\b",
        subject + " " + body,
        re.I,
    ):
        raise ValueError("All outreach copy must omit prices and guarantees")
    if first_touch and links(subject + " " + body):
        raise ValueError("First-touch subject and body must omit links")


def links(text: str) -> list[str]:
    """Find explicit and plain-domain links for copy validation."""
    return [
        m.rstrip(".,;!?)")
        for m in re.findall(
            r"(?:https?://|www\.)[^\s<>]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,63}(?:/[^\s<>]*)?",
            text,
            re.I,
        )
    ]
