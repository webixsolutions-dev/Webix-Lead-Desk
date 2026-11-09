"""Read-only Titan IMAP adapter with bounded batches and explicit UID checkpoints."""

import imaplib
import ssl
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings

MAX_MESSAGE_BYTES = 262_144


@dataclass(frozen=True)
class Incoming:
    """One bounded raw message and its provider UID."""

    uid: int
    raw: bytes


@dataclass(frozen=True)
class InboxBatch:
    """A mailbox snapshot; incomplete batches cannot authorize dispatch."""

    uidvalidity: str
    messages: list[Incoming]
    complete: bool


class IMAPUnavailable(Exception):
    """A redacted mailbox failure; freshness is invalidated by the caller."""


def _raw_messages(data: list[Any]) -> bytes:
    for item in data:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
            return item[1][:MAX_MESSAGE_BYTES]
    raise IMAPUnavailable("Mailbox returned no message content")


def _client(settings: Settings) -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(
        "imap.gmail.com" if settings.mail_provider == "gmail" else settings.titan_imap_host,
        993,
        ssl_context=ssl.create_default_context(),
        timeout=settings.mail_timeout_seconds,
    )


def read_inbox(
    settings: Settings,
    username: str,
    password: str,
    uidvalidity: str,
    last_uid: int,
    *,
    factory: Callable[[], imaplib.IMAP4] | None = None,
) -> InboxBatch:
    """Read up to 100 new messages without marking them read or deleting anything."""
    client: imaplib.IMAP4 | None = None
    try:
        client = factory() if factory else _client(settings)
        client.login(username, password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise IMAPUnavailable("Inbox cannot be selected")
        _, validity_data = client.response("UIDVALIDITY")
        validity = (validity_data[0] or b"").decode() if validity_data else ""
        if not validity.isdigit():
            raise IMAPUnavailable("Inbox UID validity unavailable")
        checkpoint = last_uid if validity == uidvalidity else 0
        status, ids = client.uid("search", "CHARSET", "UTF-8", "UID", f"{checkpoint + 1}:*")
        if status != "OK":
            raise IMAPUnavailable("Inbox search failed")
        all_ids = sorted(
            {int(value) for value in (ids[0] or b"").split() if int(value) > checkpoint}
        )
        messages: list[Incoming] = []
        for uid in all_ids[:100]:
            status, data = client.uid("fetch", str(uid), f"(BODY.PEEK[]<0.{MAX_MESSAGE_BYTES}>)")
            if status != "OK":
                raise IMAPUnavailable("Inbox message read failed")
            messages.append(Incoming(uid, _raw_messages(data)))
        return InboxBatch(validity, messages, len(all_ids) <= 100)
    except (OSError, imaplib.IMAP4.error, ValueError, IndexError, AttributeError) as error:
        raise IMAPUnavailable("Inbox sync failed") from error
    finally:
        if client is not None:
            # Cleanup cannot invalidate an already completed read.
            with suppress(OSError, imaplib.IMAP4.error):
                client.logout()


def find_sent(
    settings: Settings,
    username: str,
    password: str,
    message_id: str,
    *,
    factory: Callable[[], imaplib.IMAP4] | None = None,
) -> list[bytes]:
    """Find bounded provider Sent evidence; no result never proves non-delivery."""
    if (
        not message_id.startswith("<")
        or not message_id.endswith(">")
        or any(c in message_id for c in '\r\n"')
    ):
        raise ValueError("Invalid message identity")
    client: imaplib.IMAP4 | None = None
    try:
        client = factory() if factory else _client(settings)
        client.login(username, password)
        status, _ = client.select(
            '"[Gmail]/Sent Mail"' if settings.mail_provider == "gmail" else '"Sent"', readonly=True
        )
        if status != "OK":
            raise IMAPUnavailable("Provider Sent folder unavailable")
        status, ids = client.uid(
            "search", "CHARSET", "UTF-8", "HEADER", "Message-ID", f'"{message_id}"'
        )
        if status != "OK":
            raise IMAPUnavailable("Sent search failed")
        found: list[bytes] = []
        for uid in (ids[0] or b"").split()[:10]:
            status, data = client.uid(
                "fetch", uid.decode(), f"(BODY.PEEK[]<0.{MAX_MESSAGE_BYTES}>)"
            )
            if status == "OK":
                found.append(_raw_messages(data))
        return found
    except (OSError, imaplib.IMAP4.error, ValueError, IndexError) as error:
        raise IMAPUnavailable("Sent reconciliation unavailable") from error
    finally:
        if client is not None:
            with suppress(OSError, imaplib.IMAP4.error):
                client.logout()
