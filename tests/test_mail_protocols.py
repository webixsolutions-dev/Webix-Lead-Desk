"""Real stdlib SMTP/IMAP parsing against loopback fakes, never external providers."""

import imaplib
import smtplib
from typing import Any

import pytest

from app.core.config import Settings
from app.integrations.imap import IMAPUnavailable, find_sent, read_inbox
from app.integrations.smtp import transmit
from tests.delivery_helpers import reply_bytes
from tests.mail_servers import IMAPHandler, SMTPHandler, fake_server


@pytest.mark.parametrize("drop,expected", [(False, "accepted"), (True, "uncertain")])
def test_smtp_data_acceptance_and_lost_ack(settings: Settings, drop: bool, expected: str) -> None:
    """Lost SMTP acknowledgement is ambiguous even when the fake server has the message."""
    with fake_server(SMTPHandler, drop=drop) as server:
        result = transmit(
            settings,
            "sales@example.org",
            "synthetic",
            "service@example.com",
            reply_bytes("Synthetic outbound"),
            before_data=lambda: True,
            factory=lambda: smtplib.SMTP(
                str(server.server_address[0]), int(server.server_address[1]), timeout=2
            ),
        )
        assert result.outcome == expected and len(server.messages) == 1


def test_guard_prevents_data_even_after_authentication(settings: Settings) -> None:
    """A late reply or expired lease stops DATA without sending a body."""
    with fake_server(SMTPHandler) as server:
        result = transmit(
            settings,
            "sales@example.org",
            "synthetic",
            "service@example.com",
            b"Body",
            before_data=lambda: False,
            factory=lambda: smtplib.SMTP(
                str(server.server_address[0]), int(server.server_address[1]), timeout=2
            ),
        )
        assert result.outcome == "cancelled" and not server.messages
        assert "AUTH" in server.requests and "DATA" not in server.requests


def test_imap_read_only_checkpoint_and_sent_lookup(settings: Settings) -> None:
    """Provider reads use EXAMINE and BODY.PEEK without marking mail read or deleting it."""
    with fake_server(IMAPHandler) as server:
        server.messages = [reply_bytes("Synthetic reply", "<stable@example.org>")]

        def factory() -> imaplib.IMAP4:
            """Connect only to this loopback fake."""
            return imaplib.IMAP4(
                str(server.server_address[0]), int(server.server_address[1]), timeout=2
            )

        batch = read_inbox(settings, "sales@example.org", "synthetic", "old", 999, factory=factory)
        assert batch.complete and batch.uidvalidity == "19" and len(batch.messages) == 1
        assert any("UID 1:*" in request for request in server.requests)
        assert any("BODY.PEEK" in request for request in server.requests)
        assert any(request.startswith("EXAMINE") for request in server.requests)
        empty = read_inbox(settings, "sales@example.org", "synthetic", "19", 1, factory=factory)
        assert empty.messages == []
        found = find_sent(
            settings, "sales@example.org", "synthetic", "<stable@example.org>", factory=factory
        )
        assert found == server.messages
        assert not any("STORE" in command or "DELETE" in command for command in server.requests)


def test_imap_large_batch_is_not_complete(settings: Settings) -> None:
    """A mailbox backlog cannot authorize sending before all pending replies are scanned."""
    with fake_server(IMAPHandler) as server:
        server.messages = [reply_bytes("Test")]
        server.search_ids = b" ".join(str(index).encode() for index in range(1, 102))
        batch = read_inbox(
            settings,
            "sales@example.org",
            "synthetic",
            "19",
            0,
            factory=lambda: imaplib.IMAP4(
                str(server.server_address[0]), int(server.server_address[1]), timeout=2
            ),
        )
        assert not batch.complete and len(batch.messages) == 100


def test_provider_exceptions_are_redacted(settings: Settings) -> None:
    """Credentials and raw provider diagnostics never escape external-call wrappers."""

    def unavailable() -> Any:
        """Raise a synthetic credential-bearing failure."""
        raise OSError("private-password secret@example.org")

    assert (
        transmit(
            settings,
            "sales@example.org",
            "synthetic",
            "service@example.com",
            b"data",
            before_data=lambda: True,
            factory=unavailable,
        ).outcome
        == "temporary"
    )
    with pytest.raises(IMAPUnavailable, match="Inbox sync failed"):
        read_inbox(settings, "sales@example.org", "synthetic", "", 0, factory=unavailable)
    with pytest.raises(IMAPUnavailable, match="Sent reconciliation unavailable"):
        find_sent(
            settings, "sales@example.org", "synthetic", "<x@example.org>", factory=unavailable
        )
    with pytest.raises(ValueError, match="Invalid message"):
        find_sent(settings, "sales@example.org", "synthetic", "<a>\r\n", factory=unavailable)
