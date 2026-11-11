"""Provider boundary tests using synthetic HTTP responses, never live WhatsApp."""

import io
import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from email.message import Message
from typing import Any

import pytest

from app.whatsapp import transport


class Opener:
    """Record only synthetic requests and return a bounded in-memory response."""

    def __init__(self, body: bytes, error: Exception | None = None) -> None:
        self.body, self.error = body, error
        self.request: Any = None

    @contextmanager
    def open(self, request: Any, timeout: int) -> Iterator[io.BytesIO]:
        """Emulate urllib's response context without a socket."""
        self.request = request
        assert timeout == 45
        if self.error:
            raise self.error
        yield io.BytesIO(self.body)


def test_fixed_origin_and_bounded_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """No configurable destinations or redirects can receive the API bearer token."""
    opener = Opener(b'{"messages": [{"id": "wamid.test"}]}')
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: opener)
    assert transport.send("synthetic", "user:1234", "test") == "wamid.test"
    assert opener.request.full_url == "https://api.whatsapp.com/agent/v1/messages"
    assert json.loads(opener.request.data)["to"] == "user:1234"
    opener.body = b"x" * 1_000_001
    with pytest.raises(transport.AgentError, match="invalid_response"):
        transport.send("synthetic", "user:1234", "test")
    opener.body = b"[]"
    with pytest.raises(transport.AgentError, match="invalid_response"):
        transport.send("synthetic", "user:1234", "test")
    opener.body = b"not json"
    with pytest.raises(transport.AgentError, match="invalid_response"):
        transport.send("synthetic", "user:1234", "test")


@pytest.mark.parametrize(
    "status, expected",
    [
        (301, "delivery_unknown"),
        (401, "key_or_request_rejected"),
        (429, "rate_limited"),
        (500, "delivery_unknown"),
    ],
)
def test_redacted_http_failures(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    """Upstream response messages and request secrets never appear in stored failure text."""
    error = urllib.error.HTTPError(
        "https://example.invalid", status, "private body", Message(), None
    )
    opener = Opener(b"", error)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: opener)
    with pytest.raises(transport.AgentError) as caught:
        transport.send("synthetic", "user:1234", "test")
    assert str(caught.value) == expected


def test_invalid_shapes_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing IDs, invalid nesting and network timeouts remain conservative failures."""
    opener = Opener(b"{}")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: opener)
    with pytest.raises(transport.AgentError, match="delivery_unknown"):
        transport.send("synthetic", "user:1234", "test")
    with pytest.raises(transport.AgentError, match="invalid_key"):
        transport.send("bad\nkey", "user:1234", "test")
    opener.body = b'{"next_offset": 1, "entry": [{}]}'
    with pytest.raises(transport.AgentError, match="invalid_response"):
        transport.updates("synthetic", 0)
    opener.error = TimeoutError("private details")
    with pytest.raises(transport.AgentError, match="connection_unknown"):
        transport.updates("synthetic", 0)
