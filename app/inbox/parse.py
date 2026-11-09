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
    delivery_recipient: str = ""
    delivery_action: str = ""


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
    delivery_recipient = ""
    delivery_action = ""
    for part in message.walk():
        if part.get_content_type() == "message/delivery-status":
            content = part.get_payload()
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, Message):
                        continue
                    recipient = str(block.get("Final-Recipient", ""))
                    action = str(block.get("Action", "")).lower()
                    if recipient and action in {
                        "failed",
                        "delayed",
                        "delivered",
                        "relayed",
                        "expanded",
                    }:
                        delivery_recipient = recipient.split(";")[-1].strip().casefold()[:254]
                        delivery_action = (
                            action
                            if action != "failed" or str(block.get("Status", "")).startswith("5.")
                            else "unknown"
                        )
                        if action == "failed" and str(block.get("Status", "")).startswith("5."):
                            bounce = delivery_recipient
    text = plain_text(message)
    daemon = any(token in sender for token in ("mailer-daemon", "postmaster"))
    if daemon and not delivery_action:
        references.extend(re.findall(r"<[^<>\s]{1,250}>", text)[:30])
        if re.search(r"\b(?:5\.\d\.\d|550|551|552|553|554)\b", text) and re.search(
            r"undeliver|delivery fail|permanent|recipient.*reject|user unknown", text, re.I
        ):
            delivery_action = "failed"
            found = re.search(
                r"(?:Final-Recipient:\s*rfc822;|failed recipient:)\s*([^\s<>]+@[^\s<>]+)",
                text,
                re.I,
            )
            bounce = found[1].lower() if found else ""
        elif re.search(r"delivery delayed|temporarily delayed|will retry", text, re.I):
            delivery_action = "delayed"
    return ParsedReply(
        sender,
        str(message.get("Subject", ""))[:500],
        text,
        references,
        bounce,
        str(message.get("Auto-Submitted", "no")).lower() != "no",
        hashlib.sha256(raw).hexdigest(),
        delivery_recipient,
        delivery_action,
    )


def classify(reply: ParsedReply) -> str:
    """Label a reply for human review after all pending follow-ups have already stopped."""
    if reply.bounce_recipient or reply.delivery_action == "failed":
        return "bounce"
    if reply.delivery_action:
        return "delivery_delayed" if reply.delivery_action == "delayed" else "delivery_notice"
    if reply.automatic:
        return "automatic"
    # Ignore quoted originals, where our unsubscribe footer would create false positives.
    lines: list[str] = []
    for line in reply.text.splitlines():
        if line.startswith(">") or re.match(r"^(On .+wrote:|From:|[-_]{3,})", line, re.I):
            break
        lines.append(line)
    first = "\n".join(lines).strip().lower().replace("’", "'")[:5000]
    if re.search(r"\b(unsubscribe|remove me|stop emailing|do not contact|don't contact)\b", first):
        return "unsubscribe"
    if re.search(
        r"\b(not(?:\W+\w+){0,4}\W+interested|no thanks|no thank you|"
        r"not (?:now|right now)|no need|we're all set|already have someone|"
        r"no interest|isn't interested|aren't interested)\b",
        first,
    ):
        return "not_interested"
    if re.search(r"\b(not|never|don't|isn't|aren't|can't)\b", first):
        return "needs_review"
    if re.search(r"\b(interested|let.s talk|book a call|send (?:me )?more|sounds good)\b", first):
        return "interested"
    return "needs_review"
