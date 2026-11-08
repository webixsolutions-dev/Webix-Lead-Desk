"""Titan SMTP boundary with explicit pre-acceptance and uncertain-outcome handling."""

import smtplib
import ssl
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings


@dataclass(frozen=True)
class SMTPResult:
    """A controlled transport outcome, never raw server diagnostics."""

    outcome: Literal["accepted", "temporary", "permanent", "uncertain", "cancelled"]
    code: str


def transmit(
    settings: Settings,
    username: str,
    password: str,
    recipient: str,
    wire: bytes,
    *,
    before_data: Callable[[], bool],
    factory: Callable[[], smtplib.SMTP] | None = None,
) -> SMTPResult:
    """Send to one exact recipient once; never retry here after SMTP DATA begins."""
    client: smtplib.SMTP | None = None
    data_started = False
    try:
        client = (
            factory()
            if factory
            else smtplib.SMTP_SSL(
                settings.titan_smtp_host,
                465,
                timeout=settings.mail_timeout_seconds,
                context=ssl.create_default_context(),
            )
        )
        client.ehlo()
        client.login(username, password)
        code, _ = client.mail(username)
        if code != 250:
            return SMTPResult("temporary" if 400 <= code < 500 else "permanent", "mail_refused")
        code, _ = client.rcpt(recipient)
        if code not in {250, 251}:
            return SMTPResult(
                "temporary" if 400 <= code < 500 else "permanent", "recipient_refused"
            )
        if not before_data():
            return SMTPResult("cancelled", "policy_changed")
        data_started = True
        code, _ = client.data(wire)
        if code == 250:
            return SMTPResult("accepted", "accepted")
        return SMTPResult("temporary" if 400 <= code < 500 else "permanent", "data_refused")
    except smtplib.SMTPResponseException as error:
        # An explicit negative server reply establishes non-acceptance for this transaction.
        return SMTPResult(
            "temporary" if 400 <= error.smtp_code < 500 else "permanent", "smtp_refused"
        )
    except (OSError, smtplib.SMTPException):
        return SMTPResult("uncertain" if data_started else "temporary", "transport_interrupted")
    finally:
        if client is not None:
            # Cleanup cannot invalidate an already received SMTP result.
            with suppress(OSError):
                client.close()
