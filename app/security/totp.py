"""RFC 6238 SHA-1 authenticator codes with bounded drift and replay protection."""

import base64
import hashlib
import hmac
import secrets
import struct


def new_secret() -> str:
    """Return 160 random bits encoded for standard authenticator applications."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, counter: int, digits: int = 6) -> str:
    """Compute the RFC code for one nonnegative 30-second counter."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def match_counter(secret: str, supplied: str, timestamp: float, last_counter: int) -> int | None:
    """Accept one unused code within one adjacent step; reject malformed input."""
    if len(supplied) != 6 or not supplied.isascii() or not supplied.isdigit():
        return None
    counter = int(timestamp) // 30
    matches = [
        step
        for step in (counter, counter - 1, counter + 1)
        if step >= 0
        and step > last_counter
        and hmac.compare_digest(code_at(secret, step), supplied)
    ]
    return max(matches) if matches else None
