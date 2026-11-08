"""Mocked socket and provider boundaries; no request reaches an external network."""

import io
import json
from typing import Any
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from app.integrations import openai, public_http
from app.integrations.public_http import MAX_BYTES, PinnedConnection, PublicFetchError, fetch_public


def test_connection_pins_ip_and_verifies_original_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connecting never asks DNS again and TLS retains the intended host identity."""
    sock = MagicMock()
    factory = MagicMock(return_value=sock)
    context = MagicMock()
    monkeypatch.setattr("socket.socket", factory)
    monkeypatch.setattr("ssl.create_default_context", lambda: context)
    conn = PinnedConnection("example.com", 443, "8.8.8.8", True)
    conn.connect()
    sock.connect.assert_called_once_with(("8.8.8.8", 443))
    context.wrap_socket.assert_called_once_with(sock, server_hostname="example.com")
    assert conn.sock is context.wrap_socket.return_value
    conn.close()
    sock.connect.side_effect = OSError()
    with pytest.raises(OSError):
        PinnedConnection("example.com", 80, "8.8.8.8", False).connect()
    sock.close.assert_called()


@pytest.mark.parametrize(
    "body,encoding,content_type,blocked",
    [
        (b"<title>Example</title>", "identity", "text/html", False),
        (b"x" * (MAX_BYTES + 1), "identity", "text/html", True),
        (b"compressed", "gzip", "text/html", True),
        (b"binary", "identity", "application/octet-stream", True),
    ],
)
def test_public_response_bounds(
    monkeypatch: pytest.MonkeyPatch, body: bytes, encoding: str, content_type: str, blocked: bool
) -> None:
    """The wrapper bounds size and content, requests no compression, and closes sockets."""
    connection = MagicMock()
    stream = io.BytesIO(body)
    response = MagicMock(status=200)
    response.read1.side_effect = stream.read
    response.getheader.side_effect = lambda name, default="": {
        "Content-Encoding": encoding,
        "Content-Type": content_type,
    }.get(name, default)
    connection.getresponse.return_value = response
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr(public_http, "public_addresses", lambda host, port: ["8.8.8.8"])
    monkeypatch.setattr(public_http, "PinnedConnection", factory)
    if blocked:
        with pytest.raises(PublicFetchError):
            fetch_public("https://example.com/")
    else:
        assert "Example" in fetch_public("https://example.com/").body
    factory.assert_called_once_with("example.com", 443, "8.8.8.8", True)
    connection.close.assert_called_once()
    assert connection.request.call_args.kwargs["headers"]["Accept-Encoding"] == "identity"


def test_public_http_does_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect processing belongs to the robots-aware same-origin research service."""
    connection = MagicMock()
    response = MagicMock(status=302)
    response.read1.return_value = b""
    response.getheader.side_effect = lambda name, default="": {
        "Location": "http://127.0.0.1/",
        "Content-Type": "text/html",
    }.get(name, default)
    connection.getresponse.return_value = response
    monkeypatch.setattr(public_http, "public_addresses", lambda host, port: ["8.8.8.8"])
    monkeypatch.setattr(public_http, "PinnedConnection", lambda *a: connection)
    assert fetch_public("https://example.com/").location == "http://127.0.0.1/"
    assert connection.request.call_count == 1


@pytest.mark.parametrize("response", [{"status": "completed"}, ["invalid"]])
def test_openai_fixed_endpoint_and_no_retries(
    monkeypatch: pytest.MonkeyPatch, response: Any
) -> None:
    """An API key is sent only to the fixed HTTPS endpoint; malformed output fails closed."""
    opener = MagicMock()
    result = opener.open.return_value.__enter__.return_value
    result.read.return_value = json.dumps(response).encode()
    monkeypatch.setattr(openai, "build_opener", lambda *args: opener)
    if isinstance(response, dict):
        assert openai.create_response("synthetic-key", {"model": "test"}) == response
    else:
        with pytest.raises(openai.ModelUnavailable):
            openai.create_response("synthetic-key", {})
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://api.openai.com/v1/responses"
    assert opener.open.call_count == 1
    opener.open.side_effect = URLError("private diagnostic")
    with pytest.raises(openai.ModelUnavailable, match="review the reserved"):
        openai.create_response("synthetic-key", {})
