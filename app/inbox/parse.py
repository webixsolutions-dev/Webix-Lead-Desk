"""Treat incoming mail as data; extract plain text and conservative review labels."""

import hashlib
import re
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr


@dataclass(frozen=True)
class ParsedReply:
    """Bounded content and matching hints, never trusted commands or authorizations."""

    sender: str
    subject: str
    text: str
    references: list[str]
    bounce_recipient: str
    automatic: bool
    dedupe_key: str


def plain_text(message: Message) -> str:
    """Read text/plain parts only; never render HTML, attachments or remote images."""
    parts: list[str] = []
    for part in message.walk():
        if (
            part.get_content_type() != "text/plain"
            or part.get_content_disposition() == "attachment"
        ):
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            try:
                parts.append(
                    payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                )
            except LookupError:
                parts.append(payload.decode("utf-8", errors="replace"))
        if sum(len(value) for value in parts) > 25_000:
            break
    return "\n".join(parts)[:25_000]


def parse_reply(raw: bytes) -> ParsedReply:
    """Extract correlation hints and DSN recipient fields from a size-limited message."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    sender = parseaddr(str(message.get("From", "")))[1].casefold()[:254]
    references = re.findall(
        r"<[^<>\s]{1,250}>",
        str(message.get("In-Reply-To", "")) + " " + str(message.get("References", "")),
    )[:30]
    bounce = ""
    for part in message.walk():
        if part.get_content_type() == "message/delivery-status":
            content = part.get_payload()
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, Message):
                        continue
                    recipient = str(block.get("Final-Recipient", ""))
                    if recipient and str(block.get("Action", "")).lower() in {"failed", "delayed"}:
                        bounce = recipient.split(";")[-1].strip().casefold()[:254]
    return ParsedReply(
        sender,
        str(message.get("Subject", ""))[:500],
        plain_text(message),
        references,
        bounce,
        str(message.get("Auto-Submitted", "no")).lower() != "no",
        hashlib.sha256(raw).hexdigest(),
    )


def classify(reply: ParsedReply) -> str:
    """Label a reply for human review after all pending follow-ups have already stopped."""
    if reply.bounce_recipient:
        return "bounce"
    if reply.automatic:
        return "automatic"
    # Ignore quoted originals, where our unsubscribe footer would create false positives.
    lines: list[str] = []
    for line in reply.text.splitlines():
        if line.startswith(">") or re.match(r"^(On .+wrote:|From:|[-_]{3,})", line, re.I):
            break
        lines.append(line)
    first = "\n".join(lines).strip().lower()[:5000]
    if re.search(r"\b(unsubscribe|remove me|stop emailing|do not contact|don't contact)\b", first):
        return "unsubscribe"
    if re.search(r"\b(not interested|no thanks|no thank you)\b", first):
        return "not_interested"
    if re.search(r"\b(interested|let.s talk|book a call|send (?:me )?more|sounds good)\b", first):
        return "interested"
    return "needs_review"
