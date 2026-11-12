"""Bounded public HTTP with DNS validation and connections pinned to validated IPs."""

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.sources.validation import website_url

USER_AGENT = "WebixLeadDesk/0.3 (business website research)"
MAX_BYTES = 512_000


class PublicFetchError(Exception):
    """A redacted public-fetch failure; no credentials or raw provider body."""

    def __init__(self, message: str, *, temporary: bool = False) -> None:
        """Mark safe-to-retry transport failures without preserving response bodies."""
        super().__init__(message)
        self.temporary = temporary


@dataclass(frozen=True)
class Page:
    """A bounded response; redirects are returned, never automatically followed."""

    url: str
    status: int
    body: str
    content_type: str
    location: str = ""
    response_headers_ms: int | None = None
    html_bytes: int | None = None
    x_robots_tag: str = ""


def public_addresses(host: str, port: int) -> list[str]:
    """Reject a hostname if any resolved address is non-public or mapped IPv6."""
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise PublicFetchError("DNS unavailable", temporary=True) from error
    addresses = sorted({str(item[4][0]) for item in records})
    if not addresses:
        raise PublicFetchError("DNS returned no addresses")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if (
            not parsed.is_global
            or parsed.is_multicast
            or parsed.is_reserved
            or getattr(parsed, "ipv4_mapped", None) is not None
            or getattr(parsed, "sixtofour", None) is not None
            or getattr(parsed, "teredo", None) is not None
        ):
            raise PublicFetchError("Destination is not public")
    return addresses


class PinnedConnection(http.client.HTTPConnection):
    """Connect to a checked numeric address without performing a second DNS lookup."""

    def __init__(self, host: str, port: int, address: str, tls: bool) -> None:
        """Retain the original hostname for HTTP Host and verified TLS SNI."""
        super().__init__(host, port, timeout=8)
        self.address = address
        self.tls = tls

    def connect(self) -> None:
        """Create a numeric-address socket directly and verify the hostname in TLS."""
        family = socket.AF_INET6 if ":" in self.address else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(8)
            sock.connect((self.address, self.port))
            self.sock = (
                ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
                if self.tls
                else sock
            )
        except Exception:
            sock.close()
            raise


def fetch_public(url: str) -> Page:
    """Fetch one public page with no cookies, proxies, redirects or decompression."""
    started = time.monotonic()
    clean = website_url(url)
    parts = urlsplit(clean)
    host = parts.hostname or ""
    port = 443 if parts.scheme == "https" else 80
    address = public_addresses(host, port)[0]
    connection = PinnedConnection(host, port, address, parts.scheme == "https")
    try:
        path = parts.path + ("?" + parts.query if parts.query else "")
        connection.request(
            "GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        )
        response = connection.getresponse()
        response_headers_ms = round((time.monotonic() - started) * 1000)
        if response.getheader("Content-Encoding", "identity") not in {"", "identity"}:
            raise PublicFetchError("Compressed responses are not accepted")
        deadline = time.monotonic() + 8
        chunks = bytearray()
        while len(chunks) <= MAX_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PublicFetchError("Page exceeded the time limit")
            # HTTPResponse may own the socket after a Connection: close response.
            chunk = response.read1(min(16_384, MAX_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        body = bytes(chunks)
        if len(body) > MAX_BYTES:
            raise PublicFetchError("Page exceeds the research size limit")
        content_type = response.getheader("Content-Type", "").lower()
        if response.status == 200 and not any(
            allowed in content_type for allowed in ("text/html", "text/plain", "application/xhtml")
        ):
            raise PublicFetchError("Unsupported page type")
        return Page(
            clean,
            response.status,
            body.decode("utf-8", errors="replace"),
            content_type,
            response.getheader("Location", ""),
            response_headers_ms,
            len(body),
            response.getheader("X-Robots-Tag", "")[:300],
        )
    except (OSError, http.client.HTTPException) as error:
        raise PublicFetchError("Public website unavailable", temporary=True) from error
    finally:
        connection.close()
