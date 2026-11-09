"""Bounded DNS mail-routing preflight; never claim that MX proves a mailbox exists."""

from typing import Literal

import dns.exception
import dns.resolver

from app.sources.validation import email_address

MailRoute = Literal["mx", "implicit_mx", "no_mail", "temporary"]


def check_mail_route(address: str) -> MailRoute:
    """Check MX/null-MX with RFC implicit A/AAAA fallback and bounded DNS timeouts."""
    normalized = email_address(address)
    if not normalized:
        return "no_mail"
    domain = normalized.rsplit("@", 1)[1]
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 3
    resolver.timeout = 2
    try:
        answers = resolver.resolve(domain, "MX")
        records = [str(record.exchange) for record in answers]
        return "no_mail" if not records or "." in records else "mx"
    except dns.resolver.NXDOMAIN:
        return "no_mail"
    except dns.resolver.NoAnswer:
        for kind in ("A", "AAAA"):
            try:
                if resolver.resolve(domain, kind):
                    return "implicit_mx"
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                continue
            except dns.exception.DNSException:
                return "temporary"
        return "no_mail"
    except dns.exception.DNSException:
        return "temporary"
